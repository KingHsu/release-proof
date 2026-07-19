from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import anyio


class MCPTransportError(RuntimeError):
    """Stable, sanitized error raised at the MCP process/protocol boundary."""


class MCPToolNotAllowedError(MCPTransportError):
    """Raised before a tool outside the explicit read-only allowlist is called."""


DEFAULT_READ_ONLY_TOOLS = frozenset({"issue_read", "pull_request_read", "get_commit"})
WRITE_SHAPED_ARGUMENT_KEYS = frozenset(
    {
        "assignees",
        "body",
        "comment",
        "commit_message",
        "content",
        "labels",
        "reviewers",
        "title",
    }
)
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)"
)
_TEXT_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"),
    re.compile(
        r"(?i)((?:api[_-]?key|password|private[_-]?key|secret|token)\s*[:=]\s*)[^\s,;]+"
    ),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    ),
)


@dataclass
class StdioMCPTransport:
    """Small synchronous facade over the official MCP stdio client.

    The transport is intentionally narrow: only explicitly allowlisted read tools can
    be called. Every operation opens a short-lived MCP session, performs the protocol
    handshake, discovers tools, and then (for ``call_tool``) invokes one advertised
    tool. Process configuration is hidden from ``repr`` so credentials supplied via
    the environment are not accidentally printed.

    Async callers should use ``ainitialize``, ``alist_tools`` and ``acall_tool``.
    """

    command: str = field(repr=False)
    args: tuple[str, ...] = field(default_factory=tuple, repr=False)
    cwd: Path | None = field(default=None, repr=False)
    environment: Mapping[str, str] = field(default_factory=dict, repr=False)
    allowed_tools: frozenset[str] = DEFAULT_READ_ONLY_TOOLS
    timeout_seconds: float = 10.0
    max_request_chars: int = 12_000
    max_response_chars: int = 30_000
    max_tool_pages: int = 10

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("MCP server command must not be empty")
        if not self.allowed_tools:
            raise ValueError("at least one read-only MCP tool must be allowlisted")
        if self.timeout_seconds <= 0:
            raise ValueError("MCP timeout must be positive")
        if self.max_request_chars <= 0 or self.max_response_chars <= 0:
            raise ValueError("MCP payload limits must be positive")
        if self.max_tool_pages <= 0:
            raise ValueError("MCP tool page limit must be positive")
        self.args = tuple(self.args)
        self.environment = dict(self.environment)
        self.allowed_tools = frozenset(self.allowed_tools)
        if self.cwd is not None:
            self.cwd = Path(self.cwd).resolve(strict=True)

    def initialize(self) -> dict[str, Any]:
        return self._run_sync(self.ainitialize)

    async def ainitialize(self) -> dict[str, Any]:
        return await self._exchange("initialize")

    def list_tools(self) -> list[dict[str, Any]]:
        return self._run_sync(self.alist_tools)

    async def alist_tools(self) -> list[dict[str, Any]]:
        return await self._exchange("list_tools")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._run_sync(self.acall_tool, name, arguments)

    async def acall_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_call(name, arguments)
        return await self._exchange("call_tool", name=name, arguments=dict(arguments))

    def _run_sync(self, function: Any, *args: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return anyio.run(function, *args)
        raise MCPTransportError(
            "synchronous MCP call cannot run inside an active event loop; use the async method"
        )

    async def _exchange(
        self,
        action: Literal["initialize", "list_tools", "call_tool"],
        *,
        name: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:  # pragma: no cover - exercised in base-only installs
            raise MCPTransportError(
                "MCP support is not installed; install the project with the 'mcp' extra"
            ) from exc

        server = StdioServerParameters(
            command=self.command,
            args=list(self.args),
            env=dict(self.environment),
            cwd=self.cwd,
            encoding="utf-8",
            encoding_error_handler="replace",
        )
        try:
            with anyio.fail_after(self.timeout_seconds):
                # Server diagnostics may include credentials or local paths. Do not relay them.
                with open(os.devnull, "w", encoding="utf-8") as discarded_stderr:
                    async with stdio_client(server, errlog=discarded_stderr) as (reader, writer):
                        async with ClientSession(
                            reader,
                            writer,
                            read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                        ) as session:
                            initialized = await session.initialize()
                            if action == "initialize":
                                return self._bounded_payload(
                                    initialized.model_dump(
                                        mode="json", by_alias=True, exclude_none=True
                                    )
                                )

                            tools = await self._discover_tools(session)
                            if action == "list_tools":
                                return self._bounded_payload(tools)

                            assert name is not None  # validated by acall_tool
                            advertised = {item["name"]: item for item in tools}
                            if name not in advertised:
                                raise MCPTransportError(
                                    "allowlisted MCP tool was not advertised by the server"
                                )
                            self._reject_write_annotation(advertised[name])
                            result = await session.call_tool(
                                name,
                                arguments or {},
                                read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                            )
                            dumped = result.model_dump(
                                mode="json", by_alias=True, exclude_none=True
                            )
                            if dumped.get("isError") is True:
                                raise MCPTransportError("MCP tool returned an error result")
                            return self._bounded_payload(dumped)
        except MCPTransportError:
            raise
        except BaseException as exc:
            if self._contains_exception(exc, TimeoutError):
                message = "MCP request timed out"
            elif self._contains_exception(exc, OSError):
                message = "MCP server process could not be started"
            else:
                message = "MCP protocol request failed"
            raise MCPTransportError(message) from None

    async def _discover_tools(self, session: Any) -> list[dict[str, Any]]:
        cursor: str | None = None
        discovered: list[dict[str, Any]] = []
        for _ in range(self.max_tool_pages):
            result = await session.list_tools(cursor=cursor)
            for tool in result.tools:
                dumped = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                if dumped.get("name") in self.allowed_tools:
                    discovered.append(dumped)
            cursor = result.nextCursor
            if not cursor:
                return discovered
        raise MCPTransportError("MCP tool discovery exceeded the configured page limit")

    def _validate_call(self, name: str, arguments: dict[str, Any]) -> None:
        if name not in self.allowed_tools:
            raise MCPToolNotAllowedError("MCP tool is not in the read-only allowlist")
        if not isinstance(arguments, dict):
            raise MCPTransportError("MCP tool arguments must be an object")
        blocked = {
            str(key).lower()
            for key in self._walk_keys(arguments)
            if str(key).lower() in WRITE_SHAPED_ARGUMENT_KEYS
        }
        if blocked:
            raise MCPToolNotAllowedError("write-shaped MCP arguments are not allowed")
        try:
            serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise MCPTransportError("MCP tool arguments must be JSON serializable") from exc
        if len(serialized) > self.max_request_chars:
            raise MCPTransportError("MCP request exceeded the configured size limit")

    def _bounded_payload(self, value: Any) -> Any:
        sanitized = self._sanitize(value)
        try:
            serialized = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:  # pragma: no cover - SDK models are JSON-safe
            raise MCPTransportError("MCP response was not JSON serializable") from exc
        if len(serialized) > self.max_response_chars:
            raise MCPTransportError("MCP response exceeded the configured size limit")
        return sanitized

    @classmethod
    def _sanitize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): (
                    "[REDACTED]" if _SENSITIVE_KEY.fullmatch(str(key)) else cls._sanitize(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [cls._sanitize(item) for item in value]
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                try:
                    embedded = json.loads(stripped)
                except (TypeError, ValueError):
                    pass
                else:
                    return json.dumps(
                        cls._sanitize(embedded), ensure_ascii=False, sort_keys=True
                    )
            sanitized = value
            for pattern in _TEXT_SECRET_PATTERNS:
                sanitized = pattern.sub(
                    lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]",
                    sanitized,
                )
            return sanitized
        return value

    @staticmethod
    def _walk_keys(value: Any) -> list[str]:
        keys: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                keys.append(str(key))
                keys.extend(StdioMCPTransport._walk_keys(item))
        elif isinstance(value, (list, tuple)):
            for item in value:
                keys.extend(StdioMCPTransport._walk_keys(item))
        return keys

    @staticmethod
    def _reject_write_annotation(tool: dict[str, Any]) -> None:
        annotations = tool.get("annotations") or {}
        if annotations.get("destructiveHint") is True or annotations.get("readOnlyHint") is False:
            raise MCPToolNotAllowedError("MCP server did not mark the tool as read-only")

    @classmethod
    def _contains_exception(cls, error: BaseException, kind: type[BaseException]) -> bool:
        if isinstance(error, kind):
            return True
        nested = getattr(error, "exceptions", ())
        return any(cls._contains_exception(item, kind) for item in nested)

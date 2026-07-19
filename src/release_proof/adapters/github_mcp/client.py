from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from release_proof.domain.models import EvidenceItem, EvidenceKind


class MCPBoundaryError(ValueError):
    pass


class MCPTransport(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


DEFAULT_TOOL_MAP = {
    "get_issue": "issue_read",
    "get_pull_request": "pull_request_read",
    "get_pull_request_files": "pull_request_read",
    "get_check_runs": "get_commit",
}
ALLOWED_OPERATIONS = frozenset(DEFAULT_TOOL_MAP)


@dataclass
class GitHubMCPReadOnlyAdapter:
    """Narrow anti-corruption layer around a read-only GitHub MCP transport.

    Tool names are configurable because upstream schemas evolve. Domain code sees only
    normalized evidence and cannot request arbitrary MCP tools or write operations.
    """

    transport: MCPTransport
    tool_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOOL_MAP))
    max_payload_chars: int = 30_000

    def fetch(self, operation: str, arguments: dict[str, Any]) -> EvidenceItem:
        if operation not in ALLOWED_OPERATIONS:
            raise MCPBoundaryError("operation is not in the GitHub read-only allowlist")
        if any(key.lower() in {"body", "comment", "content", "commit_message"} for key in arguments):
            raise MCPBoundaryError("write-shaped MCP arguments are not allowed")
        tool = self.tool_map.get(operation)
        if not tool:
            raise MCPBoundaryError("read-only operation has no configured upstream tool")
        raw = self.transport.call_tool(tool, dict(arguments))
        serialized = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
        if len(serialized) > self.max_payload_chars:
            serialized = serialized[: self.max_payload_chars] + "...[truncated]"
        source = f"mcp+github://{arguments.get('owner', '')}/{arguments.get('repo', '')}/{operation}"
        stable_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
        return EvidenceItem.from_observation(
            evidence_id=f"mcp-{operation}-{stable_id}",
            kind=EvidenceKind.CI if operation == "get_check_runs" else EvidenceKind.REQUIREMENT,
            source_uri=source,
            locator=json.dumps(arguments, ensure_ascii=False, sort_keys=True),
            content=serialized,
            observed_by="github_mcp_read_only:v1",
            metadata={"operation": operation, "upstream_tool": tool},
        )


@dataclass
class FakeMCPTransport:
    responses: dict[str, Any]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))
        if name not in self.responses:
            raise RuntimeError("fake MCP response not configured")
        return self.responses[name]

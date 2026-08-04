from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError


class LLMDisabledError(RuntimeError):
    pass


class StructuredOutputError(ValueError):
    """A provider response arrived but could not satisfy the local schema contract."""

    def __init__(self, message: str, *, usage: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.usage = usage or {}


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read an Anthropic SDK block or its dictionary representation."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


class DeepSeekAnthropicClient:
    """Small provider boundary; importing it never reads or logs a key."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int = 60,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise LLMDisabledError("API key is not configured; use offline mode or set it locally")
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - dependency gate
            raise LLMDisabledError("anthropic package is not installed") from exc
        self._client = Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
        self.model = model

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 1800,
    ) -> tuple[BaseModel, dict[str, Any]]:
        tool_name = "submit_structured_response"
        input_schema = schema.model_json_schema()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0,
            # DeepSeek V4 defaults to thinking mode, which currently rejects a
            # forced Anthropic tool_choice. This path needs deterministic schema
            # submission rather than chain-of-thought tokens.
            thinking={"type": "disabled"},
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": "Submit the requested response in the exact structured form.",
                    "input_schema": input_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        response_usage = _field(response, "usage")
        metrics: dict[str, Any] = {"model": self.model}
        if response_usage is not None:
            for key in ("input_tokens", "output_tokens"):
                value = _field(response_usage, key)
                if isinstance(value, (int, float)):
                    metrics[key] = value
        payload: Any | None = None
        block_types: list[str] = []
        tool_use_count = 0
        exact_name_match = False
        for block in _field(response, "content", []):
            raw_block_type = str(_field(block, "type", "unknown"))
            block_type = raw_block_type if raw_block_type in {"text", "tool_use"} else "unknown"
            block_types.append(block_type)
            if block_type == "tool_use":
                tool_use_count += 1
            if block_type == "tool_use" and _field(block, "name") == tool_name:
                exact_name_match = True
                payload = _field(block, "input")
                break
        if payload is None:
            raw_stop_reason = str(_field(response, "stop_reason", "unknown"))
            stop_reason = (
                raw_stop_reason
                if raw_stop_reason
                in {"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"}
                else "unknown"
            )
            safe_types = ",".join(dict.fromkeys(block_types)) or "none"
            raise StructuredOutputError(
                "structured response missing required tool_use "
                f"(stop_reason={stop_reason}, block_types={safe_types}, "
                f"tool_use_count={tool_use_count}, exact_name_match={str(exact_name_match).lower()})",
                usage=metrics,
            )
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise StructuredOutputError(
                    "structured tool input was a string but not valid JSON",
                    usage=metrics,
                ) from exc
        try:
            parsed = schema.model_validate(payload)
        except ValidationError as exc:
            known_top_level_fields = set(schema.model_fields)
            safe_errors = [
                {
                    "top_level": (
                        str(item.get("loc", ())[0])
                        if item.get("loc") and str(item.get("loc", ())[0]) in known_top_level_fields
                        else "unknown"
                    ),
                    "type": str(item.get("type", "validation_error")),
                }
                for item in exc.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )[:8]
            ]
            raise StructuredOutputError(
                "structured tool input did not match the requested schema "
                f"(errors={json.dumps(safe_errors, separators=(',', ':'))})",
                usage=metrics,
            ) from exc
        return parsed, metrics

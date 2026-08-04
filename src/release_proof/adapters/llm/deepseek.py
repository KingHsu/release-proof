from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class LLMDisabledError(RuntimeError):
    pass


class StructuredOutputError(ValueError):
    """A provider response arrived but could not satisfy the local schema contract."""


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
        payload: Any | None = None
        block_types: list[str] = []
        for block in response.content:
            block_types.append(str(getattr(block, "type", "unknown")))
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == tool_name
            ):
                payload = getattr(block, "input", None)
                break
        if payload is None:
            stop_reason = str(getattr(response, "stop_reason", "unknown"))
            safe_types = ",".join(dict.fromkeys(block_types)) or "none"
            raise StructuredOutputError(
                "structured response missing required tool_use "
                f"(stop_reason={stop_reason}, block_types={safe_types})"
            )
        try:
            parsed = schema.model_validate(payload)
        except ValueError as exc:
            raise StructuredOutputError(
                "structured tool input did not match the requested schema"
            ) from exc
        usage = getattr(response, "usage", None)
        metrics = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "model": self.model,
        }
        return parsed, metrics

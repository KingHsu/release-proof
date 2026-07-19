from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass
class FakeStructuredLLM:
    """Deterministic fake that exercises the real schema/prompt call boundary."""

    responses: dict[str, dict[str, Any] | BaseModel]
    model: str = "fake-structured-llm"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 1800,
    ) -> tuple[BaseModel, dict[str, Any]]:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "schema": schema.__name__,
                "max_tokens": max_tokens,
            }
        )
        if schema.__name__ not in self.responses:
            raise RuntimeError(f"no fake response for {schema.__name__}")
        parsed = schema.model_validate(self.responses[schema.__name__])
        return parsed, {
            "input_tokens": max(1, len(user) // 4),
            "output_tokens": 32,
            "model": self.model,
        }

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

FakePayload = dict[str, Any] | BaseModel
FakeResponseFactory = Callable[[str, str, type[BaseModel]], FakePayload]


@dataclass
class FakeStructuredLLM:
    """Deterministic fake that exercises the real schema/prompt call boundary."""

    responses: dict[
        str,
        FakePayload | list[FakePayload] | FakeResponseFactory,
    ]
    model: str = "fake-structured-llm"
    calls: list[dict[str, Any]] = field(default_factory=list)
    _positions: dict[str, int] = field(default_factory=dict, init=False, repr=False)

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
        configured = self.responses[schema.__name__]
        if isinstance(configured, list):
            position = self._positions.get(schema.__name__, 0)
            if position >= len(configured):
                raise RuntimeError(f"fake responses exhausted for {schema.__name__}")
            payload = configured[position]
            self._positions[schema.__name__] = position + 1
        elif callable(configured):
            payload = configured(system, user, schema)
        else:
            payload = configured
        parsed = schema.model_validate(payload)
        return parsed, {
            "input_tokens": max(1, len(user) // 4),
            "output_tokens": 32,
            "model": self.model,
        }

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from release_proof.adapters.llm import DeepSeekAnthropicClient
from release_proof.config import Settings


class SmokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern="^ok$")
    note: str = Field(max_length=80)


def main() -> int:
    settings = Settings()
    if not settings.deepseek_api_key:
        print("DEEPSEEK_API_KEY is not configured; no request was sent.")
        return 2
    client = DeepSeekAnthropicClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    _, usage = client.structured(
        system="Return only the forced structured response. Do not reveal secrets.",
        user="Submit status 'ok' and a short note confirming schema validation.",
        schema=SmokeResponse,
        max_tokens=min(256, settings.release_proof_max_output_tokens),
    )
    print(
        json.dumps(
            {
                "model": usage.get("model", settings.deepseek_model),
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "tool_use": True,
                "schema_valid": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

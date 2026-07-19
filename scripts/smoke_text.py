from __future__ import annotations

import json

from anthropic import Anthropic

from release_proof.config import Settings


def main() -> int:
    settings = Settings()
    if not settings.deepseek_api_key:
        print("DEEPSEEK_API_KEY is not configured; no request was sent.")
        return 2
    client = Anthropic(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
    )
    response = client.messages.create(
        model=settings.deepseek_model,
        max_tokens=16,
        temperature=0,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": "Only answer OK"}],
    )
    print(
        json.dumps(
            {
                "requested_model": settings.deepseek_model,
                "response_model": getattr(response, "model", None),
                "text": "".join(
                    getattr(block, "text", "") for block in response.content
                ).strip(),
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

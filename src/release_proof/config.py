from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    Secrets are accepted from the process environment but are deliberately excluded
    from repr and never persisted by ReleaseProof.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    release_proof_offline: bool = True
    release_proof_data_dir: Path = Path("runtime")
    release_proof_project_root: Path | None = None
    release_proof_allowed_roots: str = ""
    deepseek_api_key: str = Field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com/anthropic"
    # The official Pro identifier already has a 1M context window. Unsupported names
    # may be silently mapped to Flash by the Anthropic-compatible endpoint.
    deepseek_model: str = "deepseek-v4-pro"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    release_proof_max_llm_calls: int = Field(default=6, ge=1, le=50)
    release_proof_max_output_tokens: int = Field(default=1800, ge=128, le=8192)

    @property
    def database_path(self) -> Path:
        return self.release_proof_data_dir / "release-proof.sqlite3"

    @property
    def checkpoint_path(self) -> Path:
        return self.release_proof_data_dir / "checkpoints.sqlite3"

    @property
    def generated_reports_dir(self) -> Path:
        return self.release_proof_data_dir / "reports"

    @property
    def allowed_roots(self) -> list[Path]:
        values = [item.strip() for item in self.release_proof_allowed_roots.split(";")]
        return [Path(value).resolve() for value in values if value]

    def ensure_runtime_dirs(self) -> None:
        self.release_proof_data_dir.mkdir(parents=True, exist_ok=True)
        self.generated_reports_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class ToolPolicyError(ValueError):
    """Raised when a requested read falls outside the declared boundary."""


DEFAULT_BLOCKED_NAMES = {
    ".env",
    ".env.local",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
}
DEFAULT_ALLOWED_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".graphql",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".properties",
    ".proto",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)((?:api[_-]?key|token|private[_-]?key)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


@dataclass
class ToolPolicy:
    repository_root: Path
    max_output_chars: int = 24_000
    max_file_bytes: int = 1_000_000
    max_changed_files: int = 300
    timeout_seconds: int = 10
    blocked_names: set[str] = field(default_factory=lambda: set(DEFAULT_BLOCKED_NAMES))
    allowed_suffixes: set[str] = field(default_factory=lambda: set(DEFAULT_ALLOWED_SUFFIXES))

    def __post_init__(self) -> None:
        self.repository_root = self.repository_root.resolve(strict=True)
        if not self.repository_root.is_dir():
            raise ToolPolicyError("repository root is not a directory")

    def resolve_repo_path(self, relative_path: str, *, must_exist: bool = True) -> Path:
        if not relative_path or "\x00" in relative_path:
            raise ToolPolicyError("empty or invalid path")
        candidate_text = relative_path.replace("\\", "/")
        if candidate_text.startswith("/") or re.match(r"^[A-Za-z]:", candidate_text):
            raise ToolPolicyError("absolute paths are not allowed")
        if any(part in {"..", ".git"} for part in Path(candidate_text).parts):
            raise ToolPolicyError("path traversal and .git reads are not allowed")
        if Path(candidate_text).name.lower() in self.blocked_names:
            raise ToolPolicyError("secret-like file is blocked")
        candidate = (self.repository_root / Path(candidate_text)).resolve(strict=must_exist)
        try:
            candidate.relative_to(self.repository_root)
        except ValueError as exc:
            raise ToolPolicyError("path leaves configured repository") from exc
        if must_exist and candidate.is_symlink():
            raise ToolPolicyError("symlink reads are not allowed")
        return candidate

    def validate_readable_file(self, relative_path: str) -> Path:
        candidate = self.resolve_repo_path(relative_path)
        if not candidate.is_file():
            raise ToolPolicyError("path is not a file")
        if candidate.suffix.lower() not in self.allowed_suffixes:
            raise ToolPolicyError("file extension is not in the read-only allowlist")
        if candidate.stat().st_size > self.max_file_bytes:
            raise ToolPolicyError("file exceeds configured size limit")
        return candidate

    def validate_external_report(self, report_path: str) -> Path:
        raw = Path(report_path)
        candidate = (
            raw.resolve(strict=True)
            if raw.is_absolute()
            else (self.repository_root / raw).resolve(strict=True)
        )
        try:
            candidate.relative_to(self.repository_root)
        except ValueError as exc:
            raise ToolPolicyError("report must be inside configured repository") from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise ToolPolicyError("report must be a regular file")
        if candidate.suffix.lower() not in {".xml", ".json"}:
            raise ToolPolicyError("only XML and JSON reports are accepted")
        if candidate.stat().st_size > self.max_file_bytes:
            raise ToolPolicyError("report exceeds configured size limit")
        return candidate

    def truncate_and_redact(self, text: str) -> str:
        redacted = text
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", redacted)
        if len(redacted) > self.max_output_chars:
            return redacted[: self.max_output_chars] + "\n...[truncated by tool policy]"
        return redacted

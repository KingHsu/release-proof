from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from release_proof.domain.models import ChangeSummary
from release_proof.tools.policy import ToolPolicy, ToolPolicyError


class GitToolError(RuntimeError):
    pass


@dataclass
class GitReadOnlyClient:
    policy: ToolPolicy

    @classmethod
    def for_repository(cls, repository_path: str | Path) -> GitReadOnlyClient:
        return cls(policy=ToolPolicy(Path(repository_path)))

    @property
    def root(self) -> Path:
        return self.policy.repository_root

    def _run(self, args: list[str]) -> str:
        allowed = {
            "rev-parse",
            "diff",
            "diff-tree",
            "show",
            "grep",
        }
        if not args or args[0] not in allowed:
            raise GitToolError("git subcommand is not in the read-only allowlist")
        command = ["git", "-C", str(self.root), "--no-pager", *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.policy.timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitToolError("read-only git command failed or timed out") from exc
        if completed.returncode != 0:
            safe_error = self.policy.truncate_and_redact(completed.stderr.strip())
            raise GitToolError(f"read-only git command rejected: {safe_error[:500]}")
        return self.policy.truncate_and_redact(completed.stdout)

    @staticmethod
    def _validate_ref(ref: str) -> None:
        if not ref or ref.startswith("-") or "\x00" in ref or len(ref) > 200:
            raise GitToolError("unsafe git ref")

    def resolve_ref(self, ref: str) -> str:
        self._validate_ref(ref)
        return self._run(["rev-parse", "--verify", f"{ref}^{{commit}}"]).strip()

    def list_changed_files(self, base_ref: str, head_ref: str) -> list[str]:
        base = self.resolve_ref(base_ref)
        head = self.resolve_ref(head_ref)
        output = self._run(["diff", "--name-only", "--diff-filter=ACMRT", base, head, "--"])
        files = [line.strip() for line in output.splitlines() if line.strip()]
        if len(files) > self.policy.max_changed_files:
            raise GitToolError("changed file count exceeds configured limit")
        for path in files:
            self.policy.resolve_repo_path(path, must_exist=False)
        return files

    def get_change_summary(self, base_ref: str, head_ref: str) -> ChangeSummary:
        base = self.resolve_ref(base_ref)
        head = self.resolve_ref(head_ref)
        files = self.list_changed_files(base, head)
        numstat = self._run(["diff", "--numstat", base, head, "--"])
        additions = 0
        deletions = 0
        for line in numstat.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            if parts[0].isdigit():
                additions += int(parts[0])
            if parts[1].isdigit():
                deletions += int(parts[1])
        return ChangeSummary(
            base_ref=base,
            head_ref=head,
            changed_files=files,
            additions=additions,
            deletions=deletions,
        )

    def read_diff(self, base_ref: str, head_ref: str, relative_path: str) -> str:
        base = self.resolve_ref(base_ref)
        head = self.resolve_ref(head_ref)
        path = self.policy.resolve_repo_path(relative_path, must_exist=False)
        safe_relative = path.relative_to(self.root).as_posix()
        return self._run(
            ["diff", "--unified=20", "--no-ext-diff", base, head, "--", safe_relative]
        )

    def read_file(self, revision: str, relative_path: str, start: int = 1, end: int = 240) -> str:
        resolved_revision = self.resolve_ref(revision)
        if start < 1 or end < start or end - start > 500:
            raise ToolPolicyError("invalid line range")
        path = self.policy.resolve_repo_path(relative_path, must_exist=False)
        if path.suffix.lower() not in self.policy.allowed_suffixes:
            raise ToolPolicyError("file extension is not in the read-only allowlist")
        safe_relative = path.relative_to(self.root).as_posix()
        content = self._run(["show", f"{resolved_revision}:{safe_relative}"])
        return "\n".join(content.splitlines()[start - 1 : end])

    def search_code(self, pattern: str, revision: str = "HEAD") -> list[dict[str, str | int]]:
        if not pattern or len(pattern) > 200 or "\x00" in pattern:
            raise ToolPolicyError("invalid search pattern")
        resolved_revision = self.resolve_ref(revision)
        output = self._run(
            ["grep", "-n", "-I", "--fixed-strings", "--", pattern, resolved_revision]
        )
        matches: list[dict[str, str | int]] = []
        for line in output.splitlines()[:100]:
            match = re.match(r"^(.*?):(\d+):(.*)$", line)
            if match:
                matches.append(
                    {"path": match.group(1), "line": int(match.group(2)), "excerpt": match.group(3)}
                )
        return matches


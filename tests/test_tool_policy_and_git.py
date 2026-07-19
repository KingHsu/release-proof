from __future__ import annotations

from pathlib import Path

import pytest

from release_proof.adapters.local_git import GitReadOnlyClient
from release_proof.tools.policy import ToolPolicy, ToolPolicyError
from tests.helpers import make_git_repo


def test_tool_policy_blocks_escape_and_secret(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    (repo / ".env").write_text("TOKEN=secret", encoding="utf-8")
    policy = ToolPolicy(repo)
    with pytest.raises(ToolPolicyError):
        policy.validate_readable_file("../outside.txt")
    with pytest.raises(ToolPolicyError):
        policy.validate_readable_file(".env")


def test_redaction_and_truncation(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    policy = ToolPolicy(repo, max_output_chars=60)
    output = policy.truncate_and_redact("authorization=super-secret " + "x" * 100)
    assert "super-secret" not in output
    assert "[REDACTED]" in output
    assert "truncated" in output


def test_read_only_git_summary_and_diff(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    git = GitReadOnlyClient.for_repository(repo)
    summary = git.get_change_summary("HEAD~1", "HEAD")
    assert summary.changed_files == ["src/api/health.py"]
    assert summary.additions == 1
    assert summary.deletions == 1
    diff = git.read_diff("HEAD~1", "HEAD", "src/api/health.py")
    assert "status': 'ok" in diff
    assert "starting" in diff


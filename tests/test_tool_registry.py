from __future__ import annotations

from pathlib import Path

import pytest

from release_proof.tools.registry import ReadOnlyToolRegistry, ToolCall
from tests.helpers import make_git_repo


def test_registry_exposes_only_fixed_read_schemas(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    registry = ReadOnlyToolRegistry(repo)
    assert "read_diff" in registry.schemas()
    assert not any(name.startswith(("write", "run", "delete")) for name in registry.schemas())
    observation = registry.execute(
        ToolCall(
            name="get_change_summary",
            arguments={"base_ref": "HEAD~1", "head_ref": "HEAD"},
        )
    )
    assert observation.status == "ok"
    assert observation.output["changed_files"] == ["src/api/health.py"]


def test_registry_rejects_duplicate_action_key(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    registry = ReadOnlyToolRegistry(repo)
    call = ToolCall(
        name="list_changed_files",
        arguments={"base_ref": "HEAD~1", "head_ref": "HEAD"},
    )
    assert registry.execute(call).status == "ok"
    with pytest.raises(RuntimeError, match="duplicate"):
        registry.execute(call)


def test_registry_does_not_leak_tool_error_text(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    registry = ReadOnlyToolRegistry(repo)
    observation = registry.execute(
        ToolCall(
            name="read_file",
            arguments={"revision": "HEAD", "path": ".env", "start_line": 1, "end_line": 2},
        )
    )
    assert observation.status == "error"
    assert observation.output is None
    assert observation.error_category


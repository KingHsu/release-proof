from __future__ import annotations

import json
from pathlib import Path

from release_proof.adapters.reports import ReportCollector
from release_proof.domain.models import EvidenceKind
from release_proof.tools.policy import ToolPolicy
from tests.helpers import make_git_repo, write_junit


def test_junit_preserves_pass_and_failure(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    report = write_junit(repo)
    items = ReportCollector(ToolPolicy(repo)).read(str(report), evidence_prefix="r1")
    assert [item.metadata["status"] for item in items] == ["passed", "failed"]
    assert all(item.kind == EvidenceKind.TEST_RESULT for item in items)


def test_ci_snapshot_is_read_only_evidence(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    path = repo / "reports" / "ci.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps({"jobs": [{"name": "tests", "status": "completed", "conclusion": "success"}]}),
        encoding="utf-8",
    )
    items = ReportCollector(ToolPolicy(repo)).read_ci_snapshot(str(path), evidence_prefix="ci")
    assert len(items) == 1
    assert items[0].kind == EvidenceKind.CI
    assert items[0].metadata["conclusion"] == "success"


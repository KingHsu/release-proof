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
    assert [item.metadata["status"] for item in items] == ["passed", "failed", "failed"]
    assert all(item.kind == EvidenceKind.TEST_RESULT for item in items)
    assert "test_health_api_returns_ok" in items[0].locator
    suite = items[-1]
    assert suite.metadata == {
        "name": "release-proof",
        "status": "failed",
        "tests": 2,
        "failures": 1,
        "errors": 0,
        "skipped": 0,
        "counts_consistent": True,
        "zero_failures": False,
    }


def test_junit_suite_summary_can_prove_zero_failures(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    report = repo / "reports" / "junit.xml"
    report.parent.mkdir(exist_ok=True)
    report.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="health" tests="1" failures="0">
  <testcase classname="tests.test_health" name="test_health_api_returns_ok" time="0.01" />
</testsuite>
""",
        encoding="utf-8",
    )

    items = ReportCollector(ToolPolicy(repo)).read(str(report), evidence_prefix="r2")

    assert [item.metadata["status"] for item in items] == ["passed", "passed"]
    assert items[-1].metadata["zero_failures"] is True
    assert items[-1].metadata["counts_consistent"] is True
    assert "zero_failures=true" in items[-1].content_excerpt


def test_junit_inconsistent_suite_counts_fail_closed(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    report = repo / "reports" / "junit.xml"
    report.parent.mkdir(exist_ok=True)
    report.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="health" tests="1" failures="0">
  <testcase classname="tests.test_health" name="test_health_api_returns_ok">
    <failure message="actual failure" />
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    items = ReportCollector(ToolPolicy(repo)).read(str(report), evidence_prefix="r3")

    suite = items[-1]
    assert suite.metadata["status"] == "inconsistent"
    assert suite.metadata["counts_consistent"] is False
    assert suite.metadata["zero_failures"] is False


def test_junit_invalid_declared_count_fails_closed(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    report = repo / "reports" / "junit.xml"
    report.parent.mkdir(exist_ok=True)
    report.write_text(
        """<testsuite name="health" tests="one" failures="0">
  <testcase classname="tests.test_health" name="test_health_api_returns_ok" />
</testsuite>
""",
        encoding="utf-8",
    )

    items = ReportCollector(ToolPolicy(repo)).read(str(report), evidence_prefix="r4")

    suite = items[-1]
    assert suite.metadata["status"] == "inconsistent"
    assert suite.metadata["counts_consistent"] is False
    assert suite.metadata["zero_failures"] is False


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


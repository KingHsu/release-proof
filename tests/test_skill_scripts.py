from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_script(script: Path, *args: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_api_checker_detects_removed_operation(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "paths": {
                    "/users": {"get": {"responses": {"200": {"description": "ok"}}}}
                },
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps({"openapi": "3.1.0", "paths": {"/users": {}}}),
        encoding="utf-8",
    )

    result = run_script(
        ROOT / "skills/api-compatibility-review/scripts/detect_openapi_breaks.py",
        baseline,
        candidate,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert {item["code"] for item in payload["findings"]} == {"OPERATION_REMOVED"}


def test_migration_checker_flags_destructive_and_unbounded_sql(tmp_path: Path) -> None:
    migration = tmp_path / "migration.sql"
    migration.write_text(
        "ALTER TABLE customers DROP COLUMN legacy_code;\nDELETE FROM audit_events;\n",
        encoding="utf-8",
    )

    result = run_script(
        ROOT / "skills/database-migration-review/scripts/analyze_sql_migration.py",
        migration,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    codes = {item["code"] for item in payload["findings"]}
    assert {"DROP_COLUMN", "UNBOUNDED_DELETE"} <= codes


def test_release_checker_rejects_verified_claim_without_evidence(tmp_path: Path) -> None:
    manifest = tmp_path / "release.json"
    manifest.write_text(
        json.dumps(
            {
                "release_id": "rc-1",
                "acceptance_criteria": [
                    {"id": "AC-1", "description": "Works", "status": "verified", "evidence": []}
                ],
                "required_checks": [],
                "checks": {},
                "risks": [],
            }
        ),
        encoding="utf-8",
    )

    result = run_script(
        ROOT / "skills/release-readiness-review/scripts/check_release_evidence.py",
        manifest,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ready"] is False
    assert "VERIFIED_WITHOUT_EVIDENCE" in {item["code"] for item in payload["blocking_issues"]}


def test_release_checker_accepts_complete_evidence_for_human_review(tmp_path: Path) -> None:
    manifest = tmp_path / "release.json"
    manifest.write_text(
        json.dumps(
            {
                "release_id": "rc-2",
                "acceptance_criteria": [
                    {
                        "id": "AC-1",
                        "description": "Invalid tokens are rejected",
                        "status": "verified",
                        "evidence": [{"type": "test", "ref": "ci/run/42"}],
                    }
                ],
                "required_checks": ["tests", "rollback_plan"],
                "checks": {
                    "tests": {"status": "passed", "ref": "ci/run/42"},
                    "rollback_plan": {"status": "present", "ref": "docs/runbook.md"},
                },
                "risks": [{"severity": "medium", "description": "Cache warmup", "owner": "team-a"}],
            }
        ),
        encoding="utf-8",
    )

    result = run_script(
        ROOT / "skills/release-readiness-review/scripts/check_release_evidence.py",
        manifest,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ready_for_human_review"
    assert payload["ready"] is True

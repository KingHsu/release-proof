from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_script(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_wrapper_uses_requirement_file_and_consumes_reports(tmp_path: Path) -> None:
    wrapper = load_script(
        ROOT / "skills/release-readiness-review/scripts/run_release_review.py",
        "run_release_review",
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    output_dir = tmp_path / "output"
    staging_dir = tmp_path / "staging"
    observed_command: list[str] = []
    observed_kwargs: dict[str, Any] = {}

    def fake_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed_command.extend(command)
        observed_kwargs.update(kwargs)
        requirement_index = command.index("--requirement-file") + 1
        requirement_path = Path(command[requirement_index])
        assert requirement_path.read_text(encoding="utf-8") == "- Health endpoint returns ok"

        run_id = "run-123"
        report = {
            "run_id": run_id,
            "recommendation": "conditional",
            "acceptance_matrix": [{"criterion_id": "AC-1"}],
            "domain_risks": [],
            "human_checks": [{"id": "HC-1"}],
        }
        reports_dir = Path(kwargs["env"]["RELEASE_PROOF_DATA_DIR"]) / "reports"
        reports_dir.mkdir(parents=True)
        (reports_dir / f"{run_id}.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        (reports_dir / f"{run_id}.md").write_text(
            f"# ReleaseProof report\n\nRun ID: {run_id}\n",
            encoding="utf-8",
        )
        run = {
            "run_id": run_id,
            "status": "completed",
            "report": report,
            "interrupt": None,
            "errors": [],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(run), "")

    args = argparse.Namespace(
        repository=repository,
        base="HEAD~1",
        head="HEAD",
        requirement_file=None,
        requirement="- Health endpoint returns ok",
        report=[],
        ci_snapshot=None,
        mode="auto",
        continue_without_reports=True,
        output_dir=output_dir,
        staging_dir=staging_dir,
        cli="release-proof",
        timeout_seconds=30,
    )

    exit_code, summary = wrapper.execute(args, runner=fake_runner)

    assert exit_code == 0
    assert summary["recommendation"] == "conditional"
    assert Path(summary["report_json"]).is_file()
    assert Path(summary["report_markdown"]).is_file()
    assert observed_command[0:2] == ["release-proof", "analyze"]
    assert observed_kwargs["shell"] is False
    assert not list(staging_dir.glob("release-proof-requirements-*.md"))


def test_java_scanner_flags_production_risk_patterns(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    source = repository / "src/main/java/example/jobs/SettlementJob.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """
        @Scheduled(fixedDelay = 1000)
        void sync() {
            repository.saveBatch(items);
            query("select * from records where nullable_code NOT IN (select code from filters)");
            query("select * from records where created_at >= TO_DATE(SYSDATE)");
        }
        """,
        encoding="utf-8",
    )
    script = (
        ROOT
        / "skills/java-microservice-release-review/scripts/scan_java_release_risks.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(repository),
            "--file",
            source.relative_to(repository).as_posix(),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    codes = {item["code"] for item in payload["findings"]}
    assert {
        "BATCH_IDEMPOTENCY_REVIEW",
        "ORACLE_NULL_NOT_IN_REVIEW",
        "ORACLE_DATE_INDEX_REVIEW",
    } <= codes
    assert all("evidence_pattern" in item for item in payload["findings"])


def test_java_scanner_does_not_treat_clean_scan_as_approval(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    source = repository / "src/main/java/example/Calculator.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "final class Calculator { int add(int a, int b) { return a + b; } }",
        encoding="utf-8",
    )
    script = (
        ROOT
        / "skills/java-microservice-release-review/scripts/scan_java_release_risks.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(repository),
            "--file",
            source.relative_to(repository).as_posix(),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "no_static_findings"
    assert "not release approval" in payload["notice"]


def test_java_scanner_reads_head_revision_instead_of_dirty_worktree(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()

    def git(*arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    git("init", "-q")
    git("config", "user.name", "Skill Test")
    git("config", "user.email", "skill-test@example.invalid")
    source = repository / "src/main/java/example/Query.java"
    source.parent.mkdir(parents=True)
    source.write_text("final class Query {}\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    source.write_text(
        'final class Query { String sql = "code NOT IN (select code from filters)"; }\n',
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-q", "-m", "add query")

    source.write_text(
        'final class Query { String sql = "created_at >= TO_DATE(SYSDATE)"; }\n',
        encoding="utf-8",
    )
    script = (
        ROOT
        / "skills/java-microservice-release-review/scripts/scan_java_release_risks.py"
    )

    result = subprocess.run(
        [sys.executable, str(script), str(repository), "--base", "HEAD~1", "--head", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    codes = {item["code"] for item in payload["findings"]}
    assert "ORACLE_NULL_NOT_IN_REVIEW" in codes
    assert "ORACLE_DATE_INDEX_REVIEW" not in codes
    assert payload["resolved_head"]

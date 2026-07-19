from __future__ import annotations

from pathlib import Path

import pytest

from release_proof.config import Settings
from release_proof.domain.models import (
    AnalysisRequest,
    RequirementSource,
    ResumeRequest,
    RunStatus,
)
from release_proof.graph.service import ReleaseProofService
from tests.helpers import make_git_repo, write_junit


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        release_proof_data_dir=tmp_path / "runtime",
        release_proof_offline=True,
    )


def request_for(repo: Path, **updates) -> AnalysisRequest:
    payload = {
        "repository_path": str(repo),
        "base_ref": "HEAD~1",
        "head_ref": "HEAD",
        "requirement_source": RequirementSource(
            kind="inline", content="- Health API returns an ok status"
        ),
    }
    payload.update(updates)
    return AnalysisRequest(**payload)


def test_offline_workflow_interrupt_and_resume(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    write_junit(repo)
    service = ReleaseProofService(
        settings_for(tmp_path), project_root=Path(__file__).parents[1], prefer_langgraph=False
    )
    run = service.start(request_for(repo))
    assert run.status == RunStatus.AWAITING_INPUT
    assert run.interrupt is not None
    assert "no machine-readable" in " ".join(run.interrupt.reasons)
    resumed = service.resume(run.run_id, ResumeRequest(report_paths=["reports/junit.xml"]))
    assert resumed.status == RunStatus.COMPLETED
    assert resumed.report is not None
    assert resumed.report.acceptance_matrix[0].verification_evidence
    assert service.get(run.run_id).report is not None
    assert (tmp_path / "runtime" / "reports" / f"{run.run_id}.json").exists()
    service.close()


def test_continue_without_report_is_explicitly_incomplete(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    service = ReleaseProofService(
        settings_for(tmp_path), project_root=Path(__file__).parents[1], prefer_langgraph=False
    )
    run = service.start(request_for(repo, continue_without_reports=True))
    assert run.status == RunStatus.COMPLETED
    assert run.report is not None
    assert run.report.recommendation.value in {"conditional", "not_ready"}
    assert run.report.acceptance_matrix[0].verification_evidence == []
    service.close()


def test_langgraph_sqlite_checkpoint_survives_service_restart(tmp_path: Path) -> None:
    pytest.importorskip("langgraph")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    repo = make_git_repo(tmp_path / "repo")
    report = write_junit(repo)
    settings = settings_for(tmp_path)
    first = ReleaseProofService(
        settings, project_root=Path(__file__).parents[1], prefer_langgraph=True
    )
    if first.graph is None:
        pytest.skip(first.graph_error or "LangGraph runtime unavailable")
    run = first.start(request_for(repo))
    assert run.status == RunStatus.AWAITING_INPUT
    first.close()
    second = ReleaseProofService(
        settings, project_root=Path(__file__).parents[1], prefer_langgraph=True
    )
    empty_resume = second.resume(run.run_id, ResumeRequest())
    assert empty_resume.status == RunStatus.AWAITING_INPUT
    invalid_resume = second.resume(
        run.run_id, ResumeRequest(report_paths=["reports/does-not-exist.xml"])
    )
    assert invalid_resume.status == RunStatus.AWAITING_INPUT
    resumed = second.resume(run.run_id, ResumeRequest(report_paths=[str(report)]))
    assert resumed.status == RunStatus.COMPLETED
    assert resumed.report is not None
    second.close()


def test_cross_domain_uses_multi_route(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    (repo / "migrations").mkdir()
    (repo / "migrations" / "002.sql").write_text(
        "ALTER TABLE health ADD COLUMN region TEXT; -- downgrade rollback", encoding="utf-8"
    )
    (repo / "docker-compose.yml").write_text("services: {app: {image: old}}", encoding="utf-8")
    from tests.helpers import run_git

    run_git(repo, "add", "migrations/002.sql", "docker-compose.yml")
    run_git(repo, "commit", "-q", "-m", "migration and deployment config")
    service = ReleaseProofService(
        settings_for(tmp_path), project_root=Path(__file__).parents[1], prefer_langgraph=False
    )
    request = AnalysisRequest(
        repository_path=str(repo),
        base_ref="HEAD~1",
        head_ref="HEAD",
        requirement_source=RequirementSource(
            kind="inline",
            content="- Migration preserves health data\n- Deployment rollback is documented",
        ),
        continue_without_reports=True,
        mode="auto",
    )
    run = service.start(request)
    assert run.status == RunStatus.COMPLETED
    assert run.report is not None
    assert run.report.route == "multi"
    assert "database-migration-review" in run.report.active_skills
    assert "release-readiness-review" in run.report.active_skills
    service.close()


def test_allowed_roots_are_enforced(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside_repo = make_git_repo(tmp_path / "outside" / "repo")
    settings = Settings(
        release_proof_data_dir=tmp_path / "runtime",
        release_proof_allowed_roots=str(allowed),
        release_proof_offline=True,
    )
    service = ReleaseProofService(
        settings, project_root=Path(__file__).parents[1], prefer_langgraph=False
    )
    run = service.start(request_for(outside_repo, continue_without_reports=True))
    assert run.status == RunStatus.FAILED
    assert "RELEASE_PROOF_ALLOWED_ROOTS" in " ".join(run.errors)
    service.close()


def test_tool_budget_stops_before_remaining_diffs(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    from tests.helpers import run_git

    for name in ("alpha.py", "beta.py", "gamma.py"):
        (repo / "src" / name).write_text(f"FEATURE = '{name}'\n", encoding="utf-8")
    run_git(repo, "add", "src/alpha.py", "src/beta.py", "src/gamma.py")
    run_git(repo, "commit", "-q", "-m", "add several bounded changes")
    service = ReleaseProofService(
        settings_for(tmp_path), project_root=Path(__file__).parents[1], prefer_langgraph=False
    )
    request = request_for(
        repo,
        continue_without_reports=True,
        limits={"max_tool_calls": 3},
    )
    run = service.start(request)
    assert run.status == RunStatus.COMPLETED
    assert run.report is not None
    assert run.report.stop_reason == "tool_call_limit"
    diff_refs = [item for item in run.report.evidence_index if item.kind.value == "diff"]
    assert len(diff_refs) == 1
    service.close()


def test_project_root_can_be_configured_for_wheel_or_container(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    settings = Settings(
        release_proof_data_dir=tmp_path / "runtime",
        release_proof_project_root=root,
        release_proof_offline=True,
    )
    service = ReleaseProofService(settings, prefer_langgraph=False)
    assert service.project_root == root.resolve()
    assert service.health()["project_assets"] == "ready"
    assert service.nodes.skills.discover()
    service.close()

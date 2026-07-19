from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from release_proof.adapters.github_mcp import (
    FakeMCPTransport,
    GitHubMCPReadOnlyAdapter,
    MCPBoundaryError,
)
from release_proof.api.app import create_app
from release_proof.config import Settings
from release_proof.evaluation import EvaluationRunner
from release_proof.graph.service import ReleaseProofService
from tests.helpers import make_git_repo


def test_github_mcp_adapter_only_calls_allowlisted_read_tool() -> None:
    transport = FakeMCPTransport(
        responses={"issue_read": {"number": 7, "title": "Acceptance", "body": "- works"}}
    )
    adapter = GitHubMCPReadOnlyAdapter(transport)
    evidence = adapter.fetch("get_issue", {"owner": "acme", "repo": "demo", "issue_number": 7})
    assert evidence.metadata["operation"] == "get_issue"
    assert transport.calls == [
        ("issue_read", {"owner": "acme", "repo": "demo", "issue_number": 7})
    ]
    with pytest.raises(MCPBoundaryError):
        adapter.fetch("create_issue", {"owner": "acme", "repo": "demo"})
    with pytest.raises(MCPBoundaryError):
        adapter.fetch("get_issue", {"owner": "acme", "repo": "demo", "body": "write"})


def test_offline_evaluation_shows_evidence_advantage() -> None:
    root = Path(__file__).parents[1]
    runner = EvaluationRunner()
    report = runner.run(runner.load_cases(root / "evals" / "cases"))
    assert report.cases == 8
    assert (
        report.metrics["direct"]["unsupported_claim_rate"]
        > report.metrics["single"]["unsupported_claim_rate"]
    )
    assert (
        report.metrics["gated_multi"]["critical_risk_recall"]
        > report.metrics["direct"]["critical_risk_recall"]
    )
    assert (
        report.metrics["gated_multi"]["route_accuracy"]
        > report.metrics["single"]["route_accuracy"]
    )


def test_fastapi_health_and_analysis(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    settings = Settings(
        release_proof_data_dir=tmp_path / "runtime",
        release_proof_offline=True,
    )
    service = ReleaseProofService(
        settings, project_root=Path(__file__).parents[1], prefer_langgraph=False
    )
    client = TestClient(create_app(service))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["llm_mode"] == "offline"
    response = client.post(
        "/api/v1/analyses",
        json={
            "repository_path": str(repo),
            "base_ref": "HEAD~1",
            "head_ref": "HEAD",
            "requirement_source": {
                "kind": "inline",
                "content": "- Health API returns an ok status",
            },
            "continue_without_reports": True,
        },
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]
    assert client.get(f"/api/v1/analyses/{run_id}").status_code == 200
    assert client.get(f"/api/v1/analyses/{run_id}/trace").json()["trace"]
    skills = client.get("/api/v1/skills")
    assert skills.status_code == 200
    assert {item["name"] for item in skills.json()} >= {"release-readiness-review"}
    service.close()

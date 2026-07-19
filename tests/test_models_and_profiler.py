from __future__ import annotations

import pytest
from pydantic import ValidationError

from release_proof.domain.models import (
    AnalysisRequest,
    ChangeSummary,
    RequirementSource,
    RiskDomain,
)
from release_proof.graph.profiler import choose_route, profile_change


def test_request_rejects_option_shaped_git_ref() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(
            repository_path="D:/repo",
            base_ref="--output=/tmp/x",
            requirement_source=RequirementSource(kind="inline", content="- works"),
        )


def test_simple_api_change_stays_single() -> None:
    profile = profile_change(
        ChangeSummary(
            base_ref="a",
            head_ref="b",
            changed_files=["src/api/health.py", "tests/test_health.py"],
            additions=10,
            deletions=1,
        )
    )
    assert profile.risk_domains == {RiskDomain.API_CONTRACT, RiskDomain.TESTS}
    assert choose_route(profile, "auto")[0] == "single"


def test_cross_domain_change_enables_multi() -> None:
    profile = profile_change(
        ChangeSummary(
            base_ref="a",
            head_ref="b",
            changed_files=["openapi.json", "migrations/002.sql", "docker-compose.yml"],
            additions=80,
            deletions=10,
        )
    )
    assert {
        RiskDomain.API_CONTRACT,
        RiskDomain.DATA_MIGRATION,
        RiskDomain.CONFIG_DEPLOYMENT,
    } <= profile.risk_domains
    assert choose_route(profile, "auto")[0] == "multi"


def test_docs_only_rejects_forced_multi() -> None:
    profile = profile_change(
        ChangeSummary(base_ref="a", head_ref="b", changed_files=["README.md"])
    )
    route, reasons = choose_route(profile, "multi")
    assert route == "single"
    assert "rejected" in reasons[0]

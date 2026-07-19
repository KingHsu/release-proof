from __future__ import annotations

from release_proof.domain.models import (
    AcceptanceCriterion,
    AnalysisLimits,
    CriterionStatus,
    DomainAssessment,
    EvidenceItem,
    EvidenceKind,
    Recommendation,
    RiskDomain,
)
from release_proof.domain.policy import ReleasePolicyGate
from release_proof.evidence.validator import EvidenceValidationResult
from release_proof.graph.budget import ExecutionBudget
from release_proof.graph.matrix import AcceptanceMatrixBuilder


def _criterion(critical: bool = False) -> AcceptanceCriterion:
    source = EvidenceItem.from_observation(
        evidence_id="req",
        kind=EvidenceKind.REQUIREMENT,
        source_uri="inline://req",
        locator="body",
        content="Health API returns ok",
        observed_by="test",
    )
    return AcceptanceCriterion(
        id="AC-001",
        statement="Health API returns ok",
        source_ref=source.as_ref(),
        critical=critical,
    )


def test_matrix_requires_two_evidence_layers() -> None:
    criterion = _criterion()
    diff = EvidenceItem.from_observation(
        evidence_id="diff",
        kind=EvidenceKind.DIFF,
        source_uri="git://repo",
        locator="src/api/health.py",
        content="health_api returns ok",
        observed_by="test",
    )
    partial = AcceptanceMatrixBuilder().build([criterion], [diff])[0]
    assert partial.status == CriterionStatus.PARTIALLY_SUPPORTED
    test = EvidenceItem.from_observation(
        evidence_id="test",
        kind=EvidenceKind.TEST_RESULT,
        source_uri="file://junit",
        locator="test_health_api_returns_ok",
        content="test health api returns ok status=passed",
        observed_by="test",
        metadata={"status": "passed"},
    )
    supported = AcceptanceMatrixBuilder().build([criterion], [diff, test])[0]
    assert supported.status == CriterionStatus.SUPPORTED


def test_failed_test_is_not_verification() -> None:
    criterion = _criterion()
    failed = EvidenceItem.from_observation(
        evidence_id="test",
        kind=EvidenceKind.TEST_RESULT,
        source_uri="file://junit",
        locator="test_health_api",
        content="health api returns ok status=failed",
        observed_by="test",
        metadata={"status": "failed"},
    )
    result = AcceptanceMatrixBuilder().build([criterion], [failed])[0]
    assert result.verification_evidence == []


def test_critical_unsupported_is_not_ready() -> None:
    result = AcceptanceMatrixBuilder().build([_criterion(critical=True)], [])[0]
    decision = ReleasePolicyGate().decide(
        [result],
        [],
        EvidenceValidationResult(valid=True),
    )
    assert decision.recommendation == Recommendation.NOT_READY


def test_migration_without_recovery_is_conditional() -> None:
    criterion = _criterion()
    diff = EvidenceItem.from_observation(
        evidence_id="diff",
        kind=EvidenceKind.DIFF,
        source_uri="git://repo",
        locator="health.py",
        content="health API returns ok",
        observed_by="test",
    )
    test = EvidenceItem.from_observation(
        evidence_id="test",
        kind=EvidenceKind.TEST_RESULT,
        source_uri="file://junit",
        locator="test_health_api",
        content="health API returns ok",
        observed_by="test",
    )
    result = AcceptanceMatrixBuilder().build([criterion], [diff, test])[0]
    report = DomainAssessment(
        domain=RiskDomain.DATA_MIGRATION,
        summary="migration",
        missing_evidence=["migration rollback evidence"],
        specialist="data_migration_analyst",
        status="partial",
    )
    decision = ReleasePolicyGate().decide(
        [result], [report], EvidenceValidationResult(valid=True)
    )
    assert decision.recommendation == Recommendation.CONDITIONAL


def test_budget_stops_duplicate_and_no_progress() -> None:
    budget = ExecutionBudget(AnalysisLimits(max_no_progress=2))
    assert budget.record_tool("read_diff:a")
    assert not budget.record_tool("read_diff:a")
    assert budget.stop_reason == "duplicate_tool_action"
    stalled = ExecutionBudget(AnalysisLimits(max_no_progress=2))
    assert stalled.record_step(added_evidence=0)
    assert not stalled.record_step(added_evidence=0)
    assert stalled.stop_reason == "no_progress_limit"


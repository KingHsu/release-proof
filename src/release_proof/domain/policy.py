from __future__ import annotations

from dataclasses import dataclass

from release_proof.domain.models import (
    AcceptanceResult,
    CriterionStatus,
    DomainAssessment,
    Recommendation,
    RiskDomain,
)
from release_proof.evidence.validator import EvidenceValidationResult


@dataclass(frozen=True)
class GateDecision:
    recommendation: Recommendation
    reasons: list[str]


class ReleasePolicyGate:
    """Deterministic upper bound on model/specialist recommendations."""

    version = "release-policy-v1"

    def decide(
        self,
        results: list[AcceptanceResult],
        domain_reports: list[DomainAssessment],
        validation: EvidenceValidationResult,
        *,
        collector_failed: bool = False,
        budget_exhausted: bool = False,
    ) -> GateDecision:
        reasons: list[str] = []
        if collector_failed:
            return GateDecision(Recommendation.ANALYSIS_FAILED, ["required evidence collector failed"])
        if not results:
            return GateDecision(Recommendation.ANALYSIS_FAILED, ["no acceptance criteria were produced"])
        if not validation.valid:
            return GateDecision(
                Recommendation.INSUFFICIENT_EVIDENCE,
                ["one or more report claims referenced missing or modified evidence"],
            )
        critical_unsupported = [
            item
            for item in results
            if item.critical and item.status in {CriterionStatus.UNSUPPORTED, CriterionStatus.UNABLE_TO_DETERMINE}
        ]
        if critical_unsupported:
            return GateDecision(
                Recommendation.NOT_READY,
                ["at least one critical acceptance criterion is unsupported"],
            )
        unsupported = [item for item in results if item.status == CriterionStatus.UNSUPPORTED]
        if unsupported:
            return GateDecision(
                Recommendation.NOT_READY,
                ["one or more acceptance criteria are unsupported by the bounded evidence"],
            )
        verification_missing = [item for item in results if not item.verification_evidence]
        if verification_missing:
            reasons.append("one or more acceptance criteria lack verification evidence")
        migration_missing = any(
            report.domain == RiskDomain.DATA_MIGRATION and report.missing_evidence
            for report in domain_reports
        )
        if migration_missing:
            reasons.append("migration recovery or compatibility evidence is missing")
        specialist_failed = any(report.status == "failed" for report in domain_reports)
        if specialist_failed:
            reasons.append("a specialist failed and its domain is incomplete")
        unresolved_high_risk = any(
            risk.severity in {"high", "critical"} and risk.needs_human_check
            for report in domain_reports
            for risk in report.risks
        )
        if unresolved_high_risk:
            reasons.append("a high-severity domain risk still requires human confirmation")
        if budget_exhausted:
            reasons.append("analysis stopped at a configured budget or progress limit")
        if reasons:
            return GateDecision(Recommendation.CONDITIONAL, reasons)
        return GateDecision(
            Recommendation.READY_FOR_HUMAN_REVIEW,
            ["all criteria have both implementation and verification evidence"],
        )

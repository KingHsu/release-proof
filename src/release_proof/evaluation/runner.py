from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from release_proof.domain.models import (
    AcceptanceCriterion,
    AcceptanceResult,
    ChangeSummary,
    CriterionStatus,
    CriterionType,
    EvidenceItem,
    EvidenceKind,
    RiskDomain,
)
from release_proof.graph.matrix import AcceptanceMatrixBuilder
from release_proof.graph.profiler import choose_route, profile_change


class EvalCriterion(BaseModel):
    id: str
    statement: str
    critical: bool = False
    type: CriterionType = CriterionType.FUNCTIONAL


class EvalEvidence(BaseModel):
    id: str
    kind: EvidenceKind
    locator: str
    content: str
    metadata: dict = Field(default_factory=dict)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slice: str
    criteria: list[EvalCriterion]
    evidence: list[EvalEvidence]
    changed_files: list[str]
    additions: int = 0
    deletions: int = 0
    expected_unsupported: set[str] = Field(default_factory=set)
    expected_critical_domains: set[RiskDomain] = Field(default_factory=set)
    expected_route: Literal["single", "multi"] = "single"
    pr_claims_complete: bool = True


class EvalCaseResult(BaseModel):
    case_id: str
    variant: Literal["direct", "single", "gated_multi"]
    acceptance_coverage: float
    unsupported_claims: int
    supported_claims: int
    critical_risk_hits: int
    critical_risk_total: int
    route_correct: bool
    predicted_route: str


class EvaluationReport(BaseModel):
    cases: int
    results: list[EvalCaseResult]
    metrics: dict[str, dict[str, float]]
    limitations: list[str]


class EvaluationRunner:
    def __init__(self) -> None:
        self.matrix = AcceptanceMatrixBuilder()

    def load_cases(self, cases_dir: Path) -> list[EvalCase]:
        cases: list[EvalCase] = []
        for path in sorted(cases_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                cases.extend(EvalCase.model_validate(item) for item in payload)
            else:
                cases.append(EvalCase.model_validate(payload))
        if not cases:
            raise ValueError("evaluation directory contains no JSON cases")
        return cases

    def run(self, cases: list[EvalCase]) -> EvaluationReport:
        results: list[EvalCaseResult] = []
        for case in cases:
            for variant in ("direct", "single", "gated_multi"):
                results.append(self._run_case(case, variant))
        metrics: dict[str, dict[str, float]] = {}
        for variant in ("direct", "single", "gated_multi"):
            selected = [item for item in results if item.variant == variant]
            supported = sum(item.supported_claims for item in selected)
            unsupported_claims = sum(item.unsupported_claims for item in selected)
            critical_total = sum(item.critical_risk_total for item in selected)
            metrics[variant] = {
                "acceptance_coverage": sum(item.acceptance_coverage for item in selected)
                / len(selected),
                "unsupported_claim_rate": unsupported_claims / supported if supported else 0.0,
                "critical_risk_recall": (
                    sum(item.critical_risk_hits for item in selected) / critical_total
                    if critical_total
                    else 1.0
                ),
                "route_accuracy": sum(item.route_correct for item in selected) / len(selected),
            }
        return EvaluationReport(
            cases=len(cases),
            results=results,
            metrics=metrics,
            limitations=[
                "Fixtures are controlled offline cases, not production performance claims.",
                "The direct baseline trusts PR completion claims and has no evidence tools.",
                "Latency/token comparisons require an explicitly budgeted model evaluation.",
            ],
        )

    def _run_case(
        self, case: EvalCase, variant: Literal["direct", "single", "gated_multi"]
    ) -> EvalCaseResult:
        summary = ChangeSummary(
            base_ref="fixture-base",
            head_ref="fixture-head",
            changed_files=case.changed_files,
            additions=case.additions,
            deletions=case.deletions,
        )
        profile = profile_change(summary)
        requirement_evidence = EvidenceItem.from_observation(
            evidence_id=f"{case.id}-requirement",
            kind=EvidenceKind.REQUIREMENT,
            source_uri=f"fixture://{case.id}",
            locator="criteria",
            content="\n".join(item.statement for item in case.criteria),
            observed_by="eval_fixture:v1",
        )
        criteria = [
            AcceptanceCriterion(
                id=item.id,
                statement=item.statement,
                critical=item.critical,
                type=item.type,
                source_ref=requirement_evidence.as_ref(),
            )
            for item in case.criteria
        ]
        evidence = [
            EvidenceItem.from_observation(
                evidence_id=item.id,
                kind=item.kind,
                source_uri=f"fixture://{case.id}/{item.id}",
                locator=item.locator,
                content=item.content,
                observed_by="eval_fixture:v1",
                metadata=item.metadata,
            )
            for item in case.evidence
        ]
        if variant == "direct":
            predicted = [
                AcceptanceResult(
                    criterion_id=item.id,
                    criterion=item.statement,
                    critical=item.critical,
                    status=(
                        CriterionStatus.SUPPORTED
                        if case.pr_claims_complete
                        else CriterionStatus.UNABLE_TO_DETERMINE
                    ),
                    explanation="Direct baseline used the PR completion claim without evidence tools.",
                )
                for item in criteria
            ]
            route = "single"
            detected_domains: set[RiskDomain] = set()
        else:
            predicted = self.matrix.build(criteria, evidence)
            route = (
                "single"
                if variant == "single"
                else choose_route(profile, "auto")[0]
            )
            detected_domains = profile.risk_domains
        supported_ids = {
            item.criterion_id for item in predicted if item.status == CriterionStatus.SUPPORTED
        }
        unsupported_claims = len(supported_ids & case.expected_unsupported)
        covered = sum(item.status != CriterionStatus.UNABLE_TO_DETERMINE for item in predicted)
        return EvalCaseResult(
            case_id=case.id,
            variant=variant,
            acceptance_coverage=covered / len(criteria) if criteria else 0.0,
            unsupported_claims=unsupported_claims,
            supported_claims=len(supported_ids),
            critical_risk_hits=len(case.expected_critical_domains & detected_domains),
            critical_risk_total=len(case.expected_critical_domains),
            route_correct=route == case.expected_route,
            predicted_route=route,
        )

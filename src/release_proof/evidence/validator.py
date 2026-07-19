from __future__ import annotations

from pydantic import BaseModel, Field

from release_proof.domain.models import AcceptanceResult, DomainAssessment, EvidenceItem


class EvidenceValidationResult(BaseModel):
    valid: bool
    invalid_references: list[str] = Field(default_factory=list)
    hash_mismatches: list[str] = Field(default_factory=list)


class EvidenceValidator:
    def validate(
        self,
        evidence: list[EvidenceItem],
        acceptance_results: list[AcceptanceResult],
        domain_reports: list[DomainAssessment],
    ) -> EvidenceValidationResult:
        index = {item.id: item for item in evidence}
        invalid: set[str] = set()
        hash_mismatches: set[str] = set()
        refs = []
        for result in acceptance_results:
            refs.extend(result.implementation_evidence)
            refs.extend(result.verification_evidence)
        for report in domain_reports:
            refs.extend(report.evidence_refs)
            for risk in report.risks:
                refs.extend(risk.evidence)
        for ref in refs:
            item = index.get(ref.evidence_id)
            if item is None:
                invalid.add(ref.evidence_id)
            elif item.content_hash != ref.content_hash:
                hash_mismatches.add(ref.evidence_id)
        return EvidenceValidationResult(
            valid=not invalid and not hash_mismatches,
            invalid_references=sorted(invalid),
            hash_mismatches=sorted(hash_mismatches),
        )


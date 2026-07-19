from __future__ import annotations

import re

from release_proof.domain.models import (
    AcceptanceCriterion,
    AcceptanceResult,
    CriterionStatus,
    EvidenceItem,
    EvidenceKind,
)

STOPWORDS = {
    "the",
    "and",
    "with",
    "from",
    "that",
    "this",
    "should",
    "must",
    "when",
    "provide",
    "支持",
    "必须",
    "需要",
    "能够",
    "可以",
    "进行",
}
IMPLEMENTATION_KINDS = {
    EvidenceKind.DIFF,
    EvidenceKind.FILE,
    EvidenceKind.API_DIFF,
    EvidenceKind.MIGRATION,
    EvidenceKind.CONFIG,
}
VERIFICATION_KINDS = {
    EvidenceKind.TEST_RESULT,
    EvidenceKind.COVERAGE,
    EvidenceKind.CI,
    EvidenceKind.HUMAN_INPUT,
}


def _tokens(text: str) -> set[str]:
    lowered = text.casefold()
    ascii_tokens = set(re.findall(r"[a-z_][a-z0-9_./-]{2,}", lowered))
    cjk_sequences = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    cjk_tokens: set[str] = set()
    for sequence in cjk_sequences:
        cjk_tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return (ascii_tokens | cjk_tokens) - STOPWORDS


def _related(criterion: AcceptanceCriterion, item: EvidenceItem) -> bool:
    criterion_tokens = _tokens(criterion.statement)
    evidence_text = f"{item.locator}\n{item.content_excerpt}"
    evidence_tokens = _tokens(evidence_text)
    return bool(criterion_tokens & evidence_tokens)


class AcceptanceMatrixBuilder:
    version = "matrix-v1"

    def build(
        self, criteria: list[AcceptanceCriterion], evidence: list[EvidenceItem]
    ) -> list[AcceptanceResult]:
        results: list[AcceptanceResult] = []
        has_change_evidence = any(item.kind in IMPLEMENTATION_KINDS for item in evidence)
        for criterion in criteria:
            implementation = [
                item.as_ref()
                for item in evidence
                if item.kind in IMPLEMENTATION_KINDS and _related(criterion, item)
            ][:12]
            verification = [
                item.as_ref()
                for item in evidence
                if item.kind in VERIFICATION_KINDS
                and _related(criterion, item)
                and not (
                    item.kind == EvidenceKind.TEST_RESULT
                    and item.metadata.get("status") not in {"passed", None}
                )
                and not (
                    item.kind == EvidenceKind.CI
                    and item.metadata.get("conclusion") not in {"success", "passed", None}
                )
            ][:12]
            missing: list[str] = []
            if not implementation:
                missing.append("directly related implementation evidence")
            if not verification:
                missing.append("directly related test, CI, or human verification evidence")
            if implementation and verification:
                status = CriterionStatus.SUPPORTED
                explanation = "Related implementation and verification evidence were both observed."
            elif implementation or verification:
                status = CriterionStatus.PARTIALLY_SUPPORTED
                explanation = "Only one evidence layer was observed; implementation and verification are distinct."
            elif has_change_evidence:
                status = CriterionStatus.UNSUPPORTED
                explanation = "The bounded change evidence did not support this acceptance criterion."
            else:
                status = CriterionStatus.UNABLE_TO_DETERMINE
                explanation = "No usable change evidence was available for a reliable determination."
            results.append(
                AcceptanceResult(
                    criterion_id=criterion.id,
                    criterion=criterion.statement,
                    critical=criterion.critical,
                    status=status,
                    implementation_evidence=implementation,
                    verification_evidence=verification,
                    missing_evidence=missing,
                    explanation=explanation,
                )
            )
        return results

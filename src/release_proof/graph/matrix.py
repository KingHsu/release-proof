from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from release_proof.domain.models import (
    AcceptanceCriterion,
    AcceptanceResult,
    CriterionStatus,
    CriterionType,
    EvidenceItem,
    EvidenceKind,
    EvidenceMatchDetail,
)

STOPWORDS = {
    "the",
    "an",
    "and",
    "are",
    "for",
    "with",
    "from",
    "into",
    "its",
    "not",
    "of",
    "on",
    "to",
    "that",
    "this",
    "should",
    "must",
    "when",
    "provide",
    "support",
    "supports",
    "支持",
    "必须",
    "需要",
    "能够",
    "可以",
    "进行",
}
GENERIC_TERMS = STOPWORDS | {
    "api",
    "app",
    "application",
    "change",
    "changes",
    "code",
    "data",
    "endpoint",
    "feature",
    "function",
    "result",
    "results",
    "return",
    "service",
    "status",
    "system",
    "test",
    "tests",
    "接口",
    "功能",
    "服务",
    "状态",
    "系统",
    "测试",
    "结果",
}
ALIASES: dict[str, str] = {
    "returned": "return",
    "returns": "return",
    "returning": "return",
    "tested": "test",
    "testing": "test",
    "tests": "test",
    "verified": "verify",
    "verifies": "verify",
    "verification": "verify",
    "documented": "document",
    "documentation": "document",
    "compatible": "compatibility",
    "preserves": "preserve",
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
PREFERRED_IMPLEMENTATION_KINDS: dict[CriterionType, set[EvidenceKind]] = {
    CriterionType.COMPATIBILITY: {EvidenceKind.API_DIFF},
    CriterionType.DATA: {EvidenceKind.MIGRATION},
    CriterionType.DEPLOYMENT: {EvidenceKind.CONFIG},
    CriterionType.DOCUMENTATION: {EvidenceKind.DIFF, EvidenceKind.FILE},
    CriterionType.OBSERVABILITY: {EvidenceKind.CONFIG, EvidenceKind.DIFF},
}
PREFERRED_VERIFICATION_KINDS: dict[CriterionType, set[EvidenceKind]] = {
    CriterionType.COMPATIBILITY: {EvidenceKind.TEST_RESULT, EvidenceKind.CI},
    CriterionType.DATA: {EvidenceKind.TEST_RESULT, EvidenceKind.CI},
    CriterionType.DEPLOYMENT: {EvidenceKind.CI, EvidenceKind.HUMAN_INPUT},
    CriterionType.ERROR_HANDLING: {EvidenceKind.TEST_RESULT},
    CriterionType.OBSERVABILITY: {
        EvidenceKind.TEST_RESULT,
        EvidenceKind.COVERAGE,
        EvidenceKind.CI,
    },
}


@dataclass(frozen=True)
class _ScoredMatch:
    item: EvidenceItem
    layer: Literal["implementation", "verification"]
    score: float
    confidence: Literal["medium", "high"]
    matched_terms: tuple[str, ...]
    signals: tuple[str, ...]

    def detail(self) -> EvidenceMatchDetail:
        return EvidenceMatchDetail(
            evidence_id=self.item.id,
            layer=self.layer,
            score=self.score,
            confidence=self.confidence,
            matched_terms=list(self.matched_terms),
            signals=list(self.signals),
        )


def _tokens(text: str) -> set[str]:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    lowered = camel_split.casefold()
    ascii_tokens = {
        _canonical_token(token)
        for token in re.findall(r"[a-z][a-z0-9]{1,}", lowered)
    }
    cjk_sequences = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    cjk_tokens: set[str] = set()
    for sequence in cjk_sequences:
        cjk_tokens.update(
            sequence[index : index + 2] for index in range(len(sequence) - 1)
        )
    return (ascii_tokens | cjk_tokens) - STOPWORDS


def _canonical_token(token: str) -> str:
    return ALIASES.get(token) or token


def _normalized_phrase(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.casefold()))


def _weight(term: str) -> float:
    if re.fullmatch(r"[\u4e00-\u9fff]{2}", term):
        return 1.2
    if len(term) >= 8:
        return 1.4
    if len(term) >= 5:
        return 1.2
    return 1.0


def _criterion_terms(criterion: AcceptanceCriterion) -> set[str]:
    terms = _tokens(criterion.statement)
    return terms - GENERIC_TERMS


def _explicit_criterion_link(criterion: AcceptanceCriterion, item: EvidenceItem) -> bool:
    if item.metadata.get("criterion_id") == criterion.id:
        return True
    values = item.metadata.get("criterion_ids")
    return isinstance(values, list) and criterion.id in values


def _verification_passed(item: EvidenceItem) -> bool:
    if item.kind == EvidenceKind.TEST_RESULT:
        return item.metadata.get("status") == "passed"
    if item.kind == EvidenceKind.CI:
        return item.metadata.get("conclusion") in {"success", "passed"}
    return True


def _score_match(
    criterion: AcceptanceCriterion,
    item: EvidenceItem,
    layer: Literal["implementation", "verification"],
) -> _ScoredMatch | None:
    explicit_link = _explicit_criterion_link(criterion, item)
    criterion_terms = _criterion_terms(criterion)
    if not criterion_terms and not explicit_link:
        return None

    evidence_text = f"{item.locator}\n{item.content_excerpt}"
    evidence_terms = _tokens(evidence_text)
    locator_terms = _tokens(item.locator)
    excerpt_terms = _tokens(item.content_excerpt)
    matched = criterion_terms & evidence_terms
    signals: list[str] = []

    if explicit_link:
        score = 1.0
        signals.append("explicit_criterion_metadata")
        matched = matched or {criterion.id}
    else:
        total_weight = sum(_weight(term) for term in criterion_terms)
        matched_weight = sum(_weight(term) for term in matched)
        coverage = matched_weight / total_weight if total_weight else 0.0
        locator_weight = sum(_weight(term) for term in criterion_terms & locator_terms)
        locator_coverage = locator_weight / total_weight if total_weight else 0.0
        phrase = _normalized_phrase(criterion.statement)
        phrase_match = len(phrase) >= 8 and phrase in _normalized_phrase(evidence_text)
        preferred = (
            PREFERRED_IMPLEMENTATION_KINDS
            if layer == "implementation"
            else PREFERRED_VERIFICATION_KINDS
        ).get(criterion.type, set())

        if matched:
            signals.append("weighted_term_overlap")
        if locator_coverage:
            signals.append("locator_overlap")
        if phrase_match:
            signals.append("normalized_phrase")
        if item.kind in preferred:
            signals.append("criterion_type_kind")

        enough_support = (
            phrase_match
            or (
                len(criterion_terms) == 1
                and bool(matched)
                and bool(matched & locator_terms)
                and bool(matched & excerpt_terms)
            )
            or (
                len(matched) >= 2
                and coverage >= 0.45
            )
        )
        if not enough_support:
            return None

        score = (
            0.65 * coverage
            + 0.17 * locator_coverage
            + (0.10 if phrase_match else 0.0)
            + (0.08 if item.kind in preferred else 0.0)
            + (0.05 if len(matched) >= 2 else 0.0)
        )

    threshold = 0.55 if layer == "implementation" else 0.60
    score = round(min(1.0, score), 3)
    if score < threshold:
        return None
    confidence: Literal["medium", "high"] = "high" if score >= 0.80 else "medium"
    return _ScoredMatch(
        item=item,
        layer=layer,
        score=score,
        confidence=confidence,
        matched_terms=tuple(sorted(matched)),
        signals=tuple(signals),
    )


def _rank_matches(
    criterion: AcceptanceCriterion,
    evidence: list[EvidenceItem],
    layer: Literal["implementation", "verification"],
) -> list[_ScoredMatch]:
    allowed = IMPLEMENTATION_KINDS if layer == "implementation" else VERIFICATION_KINDS
    matches = [
        match
        for item in evidence
        if item.kind in allowed
        and (layer != "verification" or _verification_passed(item))
        if (match := _score_match(criterion, item, layer)) is not None
    ]
    return sorted(matches, key=lambda value: (-value.score, value.item.id))[:12]


def _explain_layer(label: str, matches: list[_ScoredMatch]) -> str:
    if not matches:
        return f"no {label} evidence passed the score and type constraints"
    top = matches[0]
    terms = ", ".join(top.matched_terms[:5])
    return (
        f"{label} top match={top.item.id} score={top.score:.3f} "
        f"confidence={top.confidence} terms=[{terms}]"
    )


class AcceptanceMatrixBuilder:
    version = "matrix-v2"

    def build(
        self, criteria: list[AcceptanceCriterion], evidence: list[EvidenceItem]
    ) -> list[AcceptanceResult]:
        results: list[AcceptanceResult] = []
        has_change_evidence = any(item.kind in IMPLEMENTATION_KINDS for item in evidence)
        for criterion in criteria:
            implementation_matches = _rank_matches(criterion, evidence, "implementation")
            verification_matches = _rank_matches(criterion, evidence, "verification")
            implementation = [match.item.as_ref() for match in implementation_matches]
            verification = [match.item.as_ref() for match in verification_matches]
            details = [
                match.detail()
                for match in [*implementation_matches, *verification_matches]
            ]
            missing: list[str] = []
            if not implementation:
                missing.append("directly related implementation evidence")
            if not verification:
                missing.append("directly related test, CI, or human verification evidence")
            if implementation and verification:
                status = CriterionStatus.SUPPORTED
                summary = "Both evidence layers passed deterministic score and type constraints."
            elif implementation or verification:
                status = CriterionStatus.PARTIALLY_SUPPORTED
                summary = (
                    "Only one evidence layer passed; implementation and verification "
                    "remain distinct."
                )
            elif has_change_evidence:
                status = CriterionStatus.UNSUPPORTED
                summary = (
                    "The bounded change evidence did not meet the deterministic match threshold."
                )
            else:
                status = CriterionStatus.UNABLE_TO_DETERMINE
                summary = "No usable change evidence was available for a reliable determination."
            explanation = " ".join(
                [
                    summary,
                    _explain_layer("implementation", implementation_matches) + ";",
                    _explain_layer("verification", verification_matches) + ".",
                ]
            )
            results.append(
                AcceptanceResult(
                    criterion_id=criterion.id,
                    criterion=criterion.statement,
                    critical=criterion.critical,
                    status=status,
                    implementation_evidence=implementation,
                    verification_evidence=verification,
                    match_details=details,
                    missing_evidence=missing,
                    explanation=explanation,
                )
            )
        return results

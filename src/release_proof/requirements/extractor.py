from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from release_proof.domain.models import (
    AcceptanceCriterion,
    CriterionType,
    EvidenceItem,
)
from release_proof.prompts import get_prompt


class StructuredLLM(Protocol):
    model: str

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 1800,
    ) -> tuple[BaseModel, dict[str, Any]]: ...


class ExtractedCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=3, max_length=1200)
    type: CriterionType = CriterionType.FUNCTIONAL
    verification_hint: str | None = None
    ambiguity: list[str] = Field(default_factory=list)
    critical: bool = False


class ExtractedCriteriaEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[ExtractedCriterion] = Field(min_length=1, max_length=50)


@dataclass
class ExtractionOutcome:
    criteria: list[AcceptanceCriterion]
    prompt_version: str
    usage: dict[str, Any]
    model: str

TYPE_TERMS: list[tuple[CriterionType, tuple[str, ...]]] = [
    (CriterionType.ERROR_HANDLING, ("error", "失败", "异常", "重试", "降级")),
    (CriterionType.COMPATIBILITY, ("compatible", "compatibility", "兼容", "breaking")),
    (CriterionType.DATA, ("data", "database", "migration", "数据", "迁移", "字段")),
    (CriterionType.OBSERVABILITY, ("metric", "log", "trace", "监控", "日志", "指标")),
    (CriterionType.DEPLOYMENT, ("deploy", "rollback", "docker", "发布", "部署", "回滚")),
    (CriterionType.DOCUMENTATION, ("readme", "document", "文档", "说明")),
]
CRITICAL_TERMS = ("must", "critical", "blocker", "必须", "不得", "关键", "阻断")
AMBIGUOUS_TERMS = ("appropriate", "reasonable", "as needed", "尽量", "合理", "适当", "必要时")


def _criterion_type(statement: str) -> CriterionType:
    lowered = statement.lower()
    for criterion_type, terms in TYPE_TERMS:
        if any(term in lowered for term in terms):
            return criterion_type
    return CriterionType.FUNCTIONAL


class DeterministicAcceptanceExtractor:
    """Conservative offline baseline.

    It intentionally handles checklists and bullets well, and abstains from inventing
    implicit requirements. A model-backed extractor can be injected later.
    """

    version = "deterministic-v1"

    def extract_outcome(
        self, requirement_text: str, source_evidence: EvidenceItem
    ) -> ExtractionOutcome:
        return ExtractionOutcome(
            criteria=self.extract(requirement_text, source_evidence),
            prompt_version=self.version,
            usage={"input_tokens": 0, "output_tokens": 0},
            model="offline-deterministic",
        )

    def extract(
        self, requirement_text: str, source_evidence: EvidenceItem
    ) -> list[AcceptanceCriterion]:
        candidates: list[str] = []
        for raw_line in requirement_text.replace("\r\n", "\n").splitlines():
            line = raw_line.strip()
            match = re.match(r"^(?:[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)]\s+)(.+)$", line)
            if match:
                statement = match.group(1).strip().rstrip("。;")
                if len(statement) >= 3:
                    candidates.append(statement)
        if not candidates:
            paragraphs = [part.strip() for part in re.split(r"\n\s*\n", requirement_text)]
            candidates = [part for part in paragraphs if 3 <= len(part) <= 1200 and not part.startswith("#")]
        deduplicated: list[str] = []
        seen: set[str] = set()
        for statement in candidates:
            normalized = re.sub(r"\s+", " ", statement).casefold()
            if normalized not in seen:
                seen.add(normalized)
                deduplicated.append(statement)
        criteria: list[AcceptanceCriterion] = []
        for index, statement in enumerate(deduplicated[:50], start=1):
            lowered = statement.lower()
            ambiguity = [term for term in AMBIGUOUS_TERMS if term in lowered]
            critical = any(term in lowered for term in CRITICAL_TERMS)
            criteria.append(
                AcceptanceCriterion(
                    id=f"AC-{index:03d}",
                    statement=statement,
                    type=_criterion_type(statement),
                    verification_hint="Provide a directly related test, CI check, or human verification.",
                    source_ref=source_evidence.as_ref(),
                    ambiguity=ambiguity,
                    critical=critical,
                )
            )
        if not criteria:
            raise ValueError("no independently verifiable acceptance criterion found")
        return criteria


class LLMAcceptanceExtractor:
    """Online semantic extractor constrained to the same domain schema."""

    def __init__(self, llm: StructuredLLM, *, max_output_tokens: int = 1800) -> None:
        self.llm = llm
        self.max_output_tokens = max_output_tokens
        self.prompt = get_prompt("extract_acceptance_criteria")
        self.version = self.prompt.identifier

    def extract_outcome(
        self, requirement_text: str, source_evidence: EvidenceItem
    ) -> ExtractionOutcome:
        parsed, usage = self.llm.structured(
            system=self.prompt.system,
            user=self.prompt.task_template.format(requirement=requirement_text[:12_000]),
            schema=ExtractedCriteriaEnvelope,
            max_tokens=self.max_output_tokens,
        )
        envelope = ExtractedCriteriaEnvelope.model_validate(parsed)
        criteria = [
            AcceptanceCriterion(
                id=f"AC-{index:03d}",
                statement=item.statement,
                type=item.type,
                verification_hint=item.verification_hint,
                source_ref=source_evidence.as_ref(),
                ambiguity=item.ambiguity,
                critical=item.critical,
            )
            for index, item in enumerate(envelope.criteria, start=1)
        ]
        return ExtractionOutcome(
            criteria=criteria,
            prompt_version=self.prompt.identifier,
            usage=usage,
            model=self.llm.model,
        )

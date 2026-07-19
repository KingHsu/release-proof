from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from release_proof.domain.models import (
    AcceptanceCriterion,
    DomainAssessment,
    EvidenceItem,
    EvidenceKind,
    RiskDomain,
    RiskItem,
)
from release_proof.prompts import get_prompt
from release_proof.requirements.extractor import StructuredLLM


class DomainRiskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)
    needs_human_check: bool = False


class DomainAssessmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    risks: list[DomainRiskDraft] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


PROMPT_BY_DOMAIN = {
    RiskDomain.API_CONTRACT: "api_domain",
    RiskDomain.DATA_MIGRATION: "migration_domain",
    RiskDomain.TESTS: "test_domain",
    RiskDomain.CONFIG_DEPLOYMENT: "runtime_domain",
    RiskDomain.SCHEDULED_ASYNC: "business_domain",
    RiskDomain.BUSINESS_LOGIC: "business_domain",
}

SKILL_BY_DOMAIN = {
    RiskDomain.API_CONTRACT: "api-compatibility-review",
    RiskDomain.DATA_MIGRATION: "database-migration-review",
}


def _relevant_skill_context(
    domain: RiskDomain, skill_context: list[dict[str, Any]] | None
) -> list[dict[str, str]]:
    allowed = {"release-readiness-review"}
    domain_skill = SKILL_BY_DOMAIN.get(domain)
    if domain_skill:
        allowed.add(domain_skill)
    relevant: list[dict[str, str]] = []
    for item in skill_context or []:
        name = str(item.get("name", ""))
        if name not in allowed:
            continue
        relevant.append(
            {
                "name": name,
                "version": str(item.get("version", "")),
                "instructions": str(item.get("instructions", "")),
            }
        )
    return relevant


def _domain_evidence(domain: RiskDomain, evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    accepted: dict[RiskDomain, set[EvidenceKind]] = {
        RiskDomain.API_CONTRACT: {EvidenceKind.DIFF, EvidenceKind.FILE, EvidenceKind.API_DIFF},
        RiskDomain.DATA_MIGRATION: {EvidenceKind.DIFF, EvidenceKind.FILE, EvidenceKind.MIGRATION},
        RiskDomain.TESTS: {EvidenceKind.TEST_RESULT, EvidenceKind.COVERAGE, EvidenceKind.CI},
        RiskDomain.CONFIG_DEPLOYMENT: {EvidenceKind.DIFF, EvidenceKind.CONFIG, EvidenceKind.CI},
        RiskDomain.SCHEDULED_ASYNC: {EvidenceKind.DIFF, EvidenceKind.FILE, EvidenceKind.TEST_RESULT},
        RiskDomain.BUSINESS_LOGIC: {EvidenceKind.DIFF, EvidenceKind.FILE, EvidenceKind.TEST_RESULT},
        RiskDomain.DOCS_ONLY: {EvidenceKind.DIFF, EvidenceKind.FILE},
    }
    allowed = accepted.get(domain, set())
    matches = [item for item in evidence if item.kind in allowed]
    hints = {
        RiskDomain.API_CONTRACT: ("openapi", "swagger", "route", "schema", "api/"),
        RiskDomain.DATA_MIGRATION: ("migration", "alembic", ".sql", "schema"),
        RiskDomain.CONFIG_DEPLOYMENT: ("docker", "workflow", "deploy", "config", ".yml", ".yaml"),
        RiskDomain.SCHEDULED_ASYNC: ("task", "job", "worker", "scheduler", "cron"),
    }.get(domain)
    if hints:
        return [
            item
            for item in matches
            if any(hint in f"{item.locator} {item.source_uri}".lower() for hint in hints)
            or item.kind in {EvidenceKind.TEST_RESULT, EvidenceKind.COVERAGE, EvidenceKind.CI}
        ]
    return matches


@dataclass
class DeterministicSpecialist:
    domain: RiskDomain

    @property
    def name(self) -> str:
        return f"{self.domain.value}_analyst"

    def assess(
        self, criteria: list[AcceptanceCriterion], evidence: list[EvidenceItem]
    ) -> DomainAssessment:
        relevant = _domain_evidence(self.domain, evidence)
        risks: list[RiskItem] = []
        missing: list[str] = []
        text = "\n".join(item.content_excerpt.lower() for item in relevant)
        if self.domain == RiskDomain.API_CONTRACT:
            missing.append("API compatibility evidence (for example a deterministic OpenAPI diff)")
            risks.append(
                RiskItem(
                    id="RISK-API-001",
                    domain=self.domain,
                    severity="high",
                    statement="Public contract changed; backward compatibility needs explicit verification.",
                    evidence=[item.as_ref() for item in relevant[:10]],
                    needs_human_check=True,
                )
            )
        elif self.domain == RiskDomain.DATA_MIGRATION:
            has_rollback = any(term in text for term in ("downgrade", "rollback", "down migration"))
            if not has_rollback:
                missing.append("migration rollback or forward-fix evidence")
            risks.append(
                RiskItem(
                    id="RISK-DATA-001",
                    domain=self.domain,
                    severity="high" if not has_rollback else "medium",
                    statement="Schema/data migration requires ordering, compatibility, and recovery review.",
                    evidence=[item.as_ref() for item in relevant[:10]],
                    needs_human_check=not has_rollback,
                )
            )
        elif self.domain == RiskDomain.CONFIG_DEPLOYMENT:
            has_rollback = "rollback" in text or "回滚" in text
            if not has_rollback:
                missing.append("deployment rollback evidence")
            risks.append(
                RiskItem(
                    id="RISK-RUNTIME-001",
                    domain=self.domain,
                    severity="medium",
                    statement="Runtime configuration changed and needs environment-specific review.",
                    evidence=[item.as_ref() for item in relevant[:10]],
                    needs_human_check=True,
                )
            )
        elif self.domain == RiskDomain.TESTS:
            if not any(item.kind in {EvidenceKind.TEST_RESULT, EvidenceKind.CI} for item in relevant):
                missing.append("machine-readable test or CI result")
        elif self.domain == RiskDomain.SCHEDULED_ASYNC:
            missing.append("idempotency, retry, and duplicate-execution verification")
            risks.append(
                RiskItem(
                    id="RISK-ASYNC-001",
                    domain=self.domain,
                    severity="medium",
                    statement="Scheduled/async behavior can fail outside the request path.",
                    evidence=[item.as_ref() for item in relevant[:10]],
                    needs_human_check=True,
                )
            )
        summary = (
            f"Observed {len(relevant)} bounded evidence item(s) for {self.domain.value}; "
            f"reported {len(risks)} risk(s) without authorizing release."
        )
        return DomainAssessment(
            domain=self.domain,
            summary=summary,
            risks=risks,
            evidence_refs=[item.as_ref() for item in relevant[:30]],
            missing_evidence=missing,
            specialist=self.name,
            status="partial" if missing else "complete",
        )


@dataclass
class LLMSpecialist:
    domain: RiskDomain
    llm: StructuredLLM
    skill_context: list[dict[str, Any]] | None = None
    max_output_tokens: int = 1800

    @property
    def name(self) -> str:
        return f"{self.domain.value}_llm_analyst"

    def assess(
        self, criteria: list[AcceptanceCriterion], evidence: list[EvidenceItem]
    ) -> DomainAssessment:
        relevant = _domain_evidence(self.domain, evidence)[:16]
        prompt_name = PROMPT_BY_DOMAIN.get(self.domain)
        if not prompt_name or not relevant:
            return DeterministicSpecialist(self.domain).assess(criteria, evidence)
        prompt = get_prompt(prompt_name)
        relevant_skills = _relevant_skill_context(self.domain, self.skill_context)
        evidence_payload = [
            {
                "evidence_id": item.id,
                "kind": item.kind.value,
                "locator": item.locator,
                "excerpt": item.content_excerpt[:1200],
            }
            for item in relevant
        ]
        criterion_payload = [
            {"id": item.id, "statement": item.statement, "critical": item.critical}
            for item in criteria[:30]
        ]
        user = prompt.task_template.format(
            evidence={
                "criteria": criterion_payload,
                "evidence": evidence_payload,
                "review_skills": relevant_skills,
            }
        )
        try:
            parsed, usage = self.llm.structured(
                system=prompt.system,
                user=user,
                schema=DomainAssessmentDraft,
                max_tokens=self.max_output_tokens,
            )
            draft = DomainAssessmentDraft.model_validate(parsed)
        except Exception:
            fallback = DeterministicSpecialist(self.domain).assess(criteria, evidence)
            fallback.missing_evidence.append(
                "online specialist failed; deterministic evidence review was used"
            )
            fallback.status = "partial"
            return fallback
        index = {item.id: item for item in relevant}
        risks: list[RiskItem] = []
        invalid_ids: set[str] = set()
        for position, risk in enumerate(draft.risks[:20], start=1):
            valid_refs = []
            for evidence_id in risk.evidence_ids:
                item = index.get(evidence_id)
                if item is None:
                    invalid_ids.add(evidence_id)
                else:
                    valid_refs.append(item.as_ref())
            severity: Literal["low", "medium", "high", "critical"] = (
                cast(
                    Literal["low", "medium", "high", "critical"],
                    risk.severity,
                )
                if risk.severity in {"low", "medium", "high", "critical"}
                else "medium"
            )
            risks.append(
                RiskItem(
                    id=f"RISK-{self.domain.value.upper()}-{position:03d}",
                    domain=self.domain,
                    severity=severity,
                    statement=risk.statement,
                    evidence=valid_refs,
                    needs_human_check=risk.needs_human_check or not valid_refs,
                )
            )
        missing = list(draft.missing_evidence)
        if invalid_ids:
            missing.append("model referenced evidence IDs that were not in its bounded context")
        normalized_usage: dict[str, int | float | str] = {
            key: value
            for key, value in usage.items()
            if isinstance(value, (int, float, str))
        }
        return DomainAssessment(
            domain=self.domain,
            summary=draft.summary,
            risks=risks,
            evidence_refs=[item.as_ref() for item in relevant],
            missing_evidence=missing,
            specialist=self.name,
            status="partial" if missing else "complete",
            prompt_version=prompt.identifier,
            model=self.llm.model,
            usage=normalized_usage,
            skills=[item["name"] for item in relevant_skills],
        )


class SpecialistCoordinator:
    """Runs independent domain assessments; specialists share no mutable state."""

    def __init__(
        self,
        llm: StructuredLLM | None = None,
        *,
        max_output_tokens: int = 1800,
    ) -> None:
        self.llm = llm
        self.max_output_tokens = max_output_tokens

    def llm_candidate_domains(
        self,
        domains: set[RiskDomain],
        evidence: list[EvidenceItem],
    ) -> list[RiskDomain]:
        if self.llm is None:
            return []
        return [
            domain
            for domain in sorted(domains, key=lambda value: value.value)
            if domain in PROMPT_BY_DOMAIN and _domain_evidence(domain, evidence)
        ]

    def run(
        self,
        domains: set[RiskDomain],
        criteria: list[AcceptanceCriterion],
        evidence: list[EvidenceItem],
        *,
        route: str,
        skill_context: list[dict[str, Any]] | None = None,
        llm_domains: set[RiskDomain] | None = None,
    ) -> list[DomainAssessment]:
        ordered = sorted(domains, key=lambda value: value.value)
        specialists: list[DeterministicSpecialist | LLMSpecialist] = [
            (
                LLMSpecialist(
                    domain,
                    self.llm,
                    skill_context,
                    self.max_output_tokens,
                )
                if self.llm is not None
                and (llm_domains is None or domain in llm_domains)
                else DeterministicSpecialist(domain)
            )
            for domain in ordered
        ]
        if route != "multi" or len(specialists) < 2:
            return [specialist.assess(criteria, evidence) for specialist in specialists]
        reports: dict[str, DomainAssessment] = {}

        def run_one(specialist: DeterministicSpecialist | LLMSpecialist) -> DomainAssessment:
            try:
                graph = build_specialist_subgraph(
                    specialist.domain,
                    llm=self.llm if isinstance(specialist, LLMSpecialist) else None,
                    skill_context=skill_context,
                    max_output_tokens=self.max_output_tokens,
                )
            except RuntimeError:
                return specialist.assess(criteria, evidence)
            result = graph.invoke(
                {
                    "criteria": [item.model_dump(mode="json") for item in criteria],
                    "evidence": [item.model_dump(mode="json") for item in evidence],
                    "report": None,
                }
            )
            return DomainAssessment.model_validate(result["report"])

        with ThreadPoolExecutor(max_workers=min(4, len(specialists))) as executor:
            futures = {
                executor.submit(run_one, specialist): specialist.name
                for specialist in specialists
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    reports[name] = future.result()
                except Exception:  # defensive boundary: partial result is explicit
                    domain = next(item.domain for item in specialists if item.name == name)
                    reports[name] = DomainAssessment(
                        domain=domain,
                        summary="Specialist failed; no conclusion was inferred.",
                        missing_evidence=["specialist execution failed"],
                        specialist=name,
                        status="failed",
                    )
        return [reports[specialist.name] for specialist in specialists]


def build_specialist_subgraph(
    domain: RiskDomain,
    llm: StructuredLLM | None = None,
    skill_context: list[dict[str, Any]] | None = None,
    max_output_tokens: int = 1800,
):
    """Build a stateless LangGraph subgraph when LangGraph is installed.

    The production coordinator currently runs the same specialist contract through a
    bounded executor. This builder makes the subgraph boundary executable and testable
    without coupling the domain model to LangGraph.
    """

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - dependency gate
        raise RuntimeError("LangGraph is not installed") from exc

    from typing_extensions import TypedDict

    class SpecialistState(TypedDict):
        criteria: list[dict]
        evidence: list[dict]
        report: dict | None

    specialist: DeterministicSpecialist | LLMSpecialist = (
        LLMSpecialist(domain, llm, skill_context, max_output_tokens)
        if llm is not None
        else DeterministicSpecialist(domain)
    )

    def assess_node(state: SpecialistState):
        criteria = [AcceptanceCriterion.model_validate(item) for item in state["criteria"]]
        evidence = [EvidenceItem.model_validate(item) for item in state["evidence"]]
        report = specialist.assess(criteria, evidence)
        return {"report": report.model_dump(mode="json")}

    builder = StateGraph(SpecialistState)
    builder.add_node("assess", assess_node)
    builder.add_edge(START, "assess")
    builder.add_edge("assess", END)
    return builder.compile(checkpointer=False)

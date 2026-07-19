from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, cast

from release_proof.domain.models import (
    AcceptanceCriterion,
    AcceptanceResult,
    AnalysisRequest,
    ChangeProfile,
    ChangeSummary,
    DomainAssessment,
    EvidenceItem,
    EvidenceKind,
    HumanCheck,
    InterruptPayload,
    ReleaseAssessment,
    ResumeRequest,
    RiskDomain,
    TraceEvent,
)
from release_proof.domain.policy import ReleasePolicyGate
from release_proof.evidence.validator import EvidenceValidator
from release_proof.graph.collector import EvidenceCollector
from release_proof.graph.matrix import AcceptanceMatrixBuilder
from release_proof.graph.profiler import choose_route, profile_change
from release_proof.graph.skills import SkillLoader
from release_proof.graph.specialists import SpecialistCoordinator
from release_proof.requirements.extractor import (
    DeterministicAcceptanceExtractor,
    LLMAcceptanceExtractor,
    StructuredLLM,
)


class WorkflowState(TypedDict):
    run_id: str
    request: dict[str, Any]
    trace: list[dict[str, Any]]
    warnings: list[str]
    errors: list[str]
    tool_count: int
    step_count: int
    budget_exhausted: bool
    stop_reason: str
    prompt_versions: list[str]
    llm_usage: dict[str, int | float]
    llm_call_count: int
    change_summary: NotRequired[dict[str, Any]]
    requirement_text: NotRequired[str]
    requirement_evidence: NotRequired[dict[str, Any]]
    evidence: NotRequired[list[dict[str, Any]]]
    criteria: NotRequired[list[dict[str, Any]]]
    profile: NotRequired[dict[str, Any]]
    active_skills: NotRequired[list[str]]
    active_skill_context: NotRequired[list[dict[str, Any]]]
    route: NotRequired[str]
    route_reasons: NotRequired[list[str]]
    domain_reports: NotRequired[list[dict[str, Any]]]
    acceptance_results: NotRequired[list[dict[str, Any]]]
    report: NotRequired[dict[str, Any]]
    resume_payload: NotRequired[dict[str, Any]]


def _model_list(model, values: list[dict[str, Any]]):
    return [model.model_validate(value) for value in values]


def _required(state: WorkflowState, key: str) -> Any:
    value = state.get(key)  # type: ignore[literal-required]
    if value is None:
        raise ValueError(f"workflow state is missing required stage value: {key}")
    return value


class WorkflowNodes:
    """Pure-ish graph nodes shared by LangGraph and the offline fallback."""

    def __init__(
        self,
        skills_root: Path,
        allowed_roots: list[Path] | None = None,
        llm: StructuredLLM | None = None,
        max_llm_calls: int = 6,
        max_output_tokens: int = 1800,
    ) -> None:
        self.collector = EvidenceCollector()
        self.offline_extractor = DeterministicAcceptanceExtractor()
        self.llm = llm
        self.extractor = (
            LLMAcceptanceExtractor(llm, max_output_tokens=max_output_tokens)
            if llm is not None
            else self.offline_extractor
        )
        self.skills = SkillLoader(skills_root)
        self.specialists = SpecialistCoordinator(
            llm=llm,
            max_output_tokens=max_output_tokens,
        )
        self.matrix = AcceptanceMatrixBuilder()
        self.validator = EvidenceValidator()
        self.policy_gate = ReleasePolicyGate()
        self.allowed_roots = [path.resolve() for path in (allowed_roots or [])]
        self.max_llm_calls = max_llm_calls

    @staticmethod
    def _trace(
        state: WorkflowState,
        node: str,
        summary: str,
        *,
        status: Literal["started", "completed", "paused", "failed", "skipped"] = "completed",
        prompt_version: str | None = None,
        model: str | None = None,
        usage: Mapping[str, int | float | str] | None = None,
        skills: list[str] | None = None,
    ) -> list[dict]:
        step = int(state.get("step_count", 0)) + 1
        event = TraceEvent(
            step=step,
            node=node,
            status=status,
            summary=summary,
            evidence_ids=[],
            prompt_version=prompt_version,
            model=model,
            usage=dict(usage or {}),
            skills=skills or [],
        )
        return [*state.get("trace", []), event.model_dump(mode="json")]

    def validate_request(self, state: WorkflowState) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        root = Path(request.repository_path).resolve(strict=True)
        if not (root / ".git").exists():
            raise ValueError("repository_path must point to a local Git worktree")
        if self.allowed_roots:
            in_allowed_root = False
            for allowed_root in self.allowed_roots:
                try:
                    root.relative_to(allowed_root)
                    in_allowed_root = True
                    break
                except ValueError:
                    continue
            if not in_allowed_root:
                raise ValueError("repository_path is outside RELEASE_PROOF_ALLOWED_ROOTS")
        state["request"] = request.model_dump(mode="json")
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "validate_request",
            "Repository, refs, and request schema passed the read-only boundary check.",
        )
        return state

    def collect_change_facts(self, state: WorkflowState) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        facts = self.collector.collect(request)
        state.update(
            {
                "change_summary": facts.change_summary.model_dump(mode="json"),
                "requirement_text": facts.requirement_text,
                "requirement_evidence": facts.requirement_evidence.model_dump(mode="json"),
                "evidence": [item.model_dump(mode="json") for item in facts.evidence],
                "tool_count": facts.tool_count,
                "warnings": [*state.get("warnings", []), *facts.warnings],
            }
        )
        if facts.stop_reason:
            state["budget_exhausted"] = True
            state["stop_reason"] = facts.stop_reason
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "collect_change_facts",
            f"Collected {len(facts.evidence)} bounded evidence items with read-only tools.",
        )
        return state

    def extract_acceptance_criteria(self, state: WorkflowState) -> WorkflowState:
        source = EvidenceItem.model_validate(_required(state, "requirement_evidence"))
        llm_calls = int(state.get("llm_call_count", 0))
        if self.llm is not None and llm_calls >= self.max_llm_calls:
            outcome = self.offline_extractor.extract_outcome(
                _required(state, "requirement_text"), source
            )
            state["warnings"] = [
                *state.get("warnings", []),
                "LLM call limit reached before acceptance extraction; offline baseline used",
            ]
        else:
            if self.llm is not None:
                state["llm_call_count"] = llm_calls + 1
            try:
                outcome = self.extractor.extract_outcome(
                    _required(state, "requirement_text"), source
                )
            except Exception as exc:
                outcome = self.offline_extractor.extract_outcome(
                    _required(state, "requirement_text"), source
                )
                state["warnings"] = [
                    *state.get("warnings", []),
                    f"online acceptance extraction failed ({type(exc).__name__}); offline baseline used",
                ]
        criteria = outcome.criteria
        state["criteria"] = [item.model_dump(mode="json") for item in criteria]
        state["prompt_versions"] = [
            *state.get("prompt_versions", []),
            outcome.prompt_version,
        ]
        for key in ("input_tokens", "output_tokens"):
            value = outcome.usage.get(key, 0)
            if isinstance(value, (int, float)):
                state["llm_usage"][key] = state.get("llm_usage", {}).get(key, 0) + value
        state["llm_usage"]["calls"] = int(state.get("llm_call_count", 0))
        normalized_usage = {
            key: value
            for key, value in outcome.usage.items()
            if isinstance(value, (int, float, str))
        }
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "extract_acceptance_criteria",
            f"Extracted {len(criteria)} independently assessable criteria with {outcome.prompt_version}.",
            prompt_version=outcome.prompt_version,
            model=outcome.model,
            usage=normalized_usage,
        )
        return state

    def profile_change(self, state: WorkflowState) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        summary = ChangeSummary.model_validate(_required(state, "change_summary"))
        profile = profile_change(summary)
        route, reasons = choose_route(profile, request.mode)
        state["profile"] = profile.model_dump(mode="json")
        state["route"] = route
        state["route_reasons"] = reasons
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "profile_change",
            f"Detected {len(profile.risk_domains)} risk domain(s); selected {route} route.",
        )
        return state

    def missing_context_reasons(self, state: WorkflowState) -> list[str]:
        request = AnalysisRequest.model_validate(state["request"])
        criteria = _model_list(AcceptanceCriterion, state.get("criteria", []))
        evidence = _model_list(EvidenceItem, state.get("evidence", []))
        reasons: list[str] = []
        clarifications = state.get("resume_payload", {}).get("clarifications", {})
        if any(item.ambiguity for item in criteria) and not clarifications:
            reasons.append("one or more acceptance criteria contain ambiguous language")
        has_verification = any(
            item.kind in {EvidenceKind.TEST_RESULT, EvidenceKind.COVERAGE, EvidenceKind.CI}
            for item in evidence
        )
        if request.require_verification_evidence and not has_verification:
            reasons.append("no machine-readable test, coverage, or CI evidence was supplied")
        return reasons

    def apply_resume(self, state: WorkflowState, resume: ResumeRequest) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        merged = request.model_copy(
            update={
                "report_paths": [*request.report_paths, *resume.report_paths],
                "ci_snapshot_path": resume.ci_snapshot_path or request.ci_snapshot_path,
                "continue_without_reports": resume.continue_without_reports,
            }
        )
        state["request"] = merged.model_dump(mode="json")
        state["resume_payload"] = resume.model_dump(mode="json")
        return state

    def refresh_after_resume(self, state: WorkflowState) -> WorkflowState:
        payload = state.get("resume_payload")
        if not payload:
            return state
        resume = ResumeRequest.model_validate(payload)
        if resume.report_paths or resume.ci_snapshot_path:
            refreshed = self.collect_change_facts(state)
            state.update(refreshed)
        if resume.clarifications:
            evidence = _model_list(EvidenceItem, state.get("evidence", []))
            for index, (question, answer) in enumerate(sorted(resume.clarifications.items()), start=1):
                evidence.append(
                    EvidenceItem.from_observation(
                        evidence_id=f"human-{index}",
                        kind=EvidenceKind.HUMAN_INPUT,
                        source_uri="human://resume",
                        locator=question,
                        content=answer,
                        observed_by="human_input:v1",
                    )
                )
            state["evidence"] = [item.model_dump(mode="json") for item in evidence]
        return state

    def load_relevant_skills(self, state: WorkflowState) -> WorkflowState:
        profile = ChangeProfile.model_validate(_required(state, "profile"))
        active = self.skills.activate(profile)
        state["active_skills"] = [skill.name for skill in active]
        state["active_skill_context"] = [
            {
                "name": skill.name,
                "version": skill.version,
                "instructions": self.skills.read_instructions(skill, max_chars=4000),
            }
            for skill in active
        ]
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "load_relevant_skills",
            f"Activated {len(active)} skill(s) using deterministic change rules.",
            skills=[skill.name for skill in active],
        )
        return state

    def route_analysis(self, state: WorkflowState) -> WorkflowState:
        profile = ChangeProfile.model_validate(_required(state, "profile"))
        criteria = _model_list(AcceptanceCriterion, _required(state, "criteria"))
        evidence = _model_list(EvidenceItem, _required(state, "evidence"))
        candidates = self.specialists.llm_candidate_domains(profile.risk_domains, evidence)
        available_calls = max(
            0,
            self.max_llm_calls - int(state.get("llm_call_count", 0)),
        )
        llm_domains = set(candidates[:available_calls])
        blocked_domains = candidates[available_calls:]
        if llm_domains:
            state["llm_call_count"] = int(state.get("llm_call_count", 0)) + len(llm_domains)
        state["llm_usage"]["calls"] = int(state.get("llm_call_count", 0))
        if blocked_domains:
            state["warnings"] = [
                *state.get("warnings", []),
                "LLM call limit reached; deterministic specialist fallback used for: "
                + ", ".join(domain.value for domain in blocked_domains),
            ]
        reports = self.specialists.run(
            profile.risk_domains,
            criteria,
            evidence,
            route=state.get("route", "single"),
            skill_context=state.get("active_skill_context", []),
            llm_domains=llm_domains,
        )
        state["domain_reports"] = [item.model_dump(mode="json") for item in reports]
        specialist_prompt_versions = [
            report.prompt_version for report in reports if report.prompt_version
        ]
        state["prompt_versions"] = list(
            dict.fromkeys([*state.get("prompt_versions", []), *specialist_prompt_versions])
        )
        specialist_usage: dict[str, int | float] = {}
        specialist_skills = list(
            dict.fromkeys(skill for report in reports for skill in report.skills)
        )
        for report in reports:
            for key in ("input_tokens", "output_tokens"):
                value = report.usage.get(key, 0)
                if isinstance(value, (int, float)):
                    specialist_usage[key] = specialist_usage.get(key, 0) + value
                    state["llm_usage"][key] = state.get("llm_usage", {}).get(key, 0) + value
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "route_analysis",
            f"Completed {len(reports)} structured domain assessment(s) via "
            f"{state.get('route', 'single')} route; scheduled {len(llm_domains)} bounded LLM call(s).",
            prompt_version=",".join(specialist_prompt_versions) or None,
            model=next((report.model for report in reports if report.model), None),
            usage=specialist_usage,
            skills=specialist_skills,
        )
        return state

    def build_acceptance_matrix(self, state: WorkflowState) -> WorkflowState:
        criteria = _model_list(AcceptanceCriterion, _required(state, "criteria"))
        evidence = _model_list(EvidenceItem, _required(state, "evidence"))
        results = self.matrix.build(criteria, evidence)
        state["acceptance_results"] = [item.model_dump(mode="json") for item in results]
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "build_acceptance_matrix",
            f"Mapped {len(results)} criteria to implementation and verification evidence.",
        )
        return state

    def write_report(self, state: WorkflowState) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        if int(state.get("step_count", 0)) >= request.limits.max_steps:
            state["budget_exhausted"] = True
            state["stop_reason"] = "step_limit"
        if int(state.get("tool_count", 0)) >= request.limits.max_tool_calls:
            state["budget_exhausted"] = True
            state["stop_reason"] = "tool_call_limit"
        summary = ChangeSummary.model_validate(_required(state, "change_summary"))
        profile = ChangeProfile.model_validate(_required(state, "profile"))
        evidence = _model_list(EvidenceItem, _required(state, "evidence"))
        results = _model_list(AcceptanceResult, _required(state, "acceptance_results"))
        domain_reports = _model_list(DomainAssessment, state.get("domain_reports", []))
        validation = self.validator.validate(evidence, results, domain_reports)
        decision = self.policy_gate.decide(
            results,
            domain_reports,
            validation,
            budget_exhausted=bool(state.get("budget_exhausted")),
        )
        missing = sorted(
            {
                item
                for result in results
                for item in result.missing_evidence
            }
            | {item for report in domain_reports for item in report.missing_evidence}
        )
        risks = [risk for report in domain_reports for risk in report.risks]
        human_checks = [
            HumanCheck(
                id=f"HC-{index:03d}",
                question=missing_item,
                reason="The available evidence cannot resolve this check automatically.",
                blocking="rollback" in missing_item.lower(),
            )
            for index, missing_item in enumerate(missing, start=1)
        ]
        rollback_notes: list[str] = []
        if RiskDomain.DATA_MIGRATION in profile.risk_domains:
            rollback_notes.append("Have a reviewed rollback or forward-fix plan for data migration changes.")
        if RiskDomain.CONFIG_DEPLOYMENT in profile.risk_domains:
            rollback_notes.append("Record the previous configuration and a bounded rollback trigger.")
        limitations = [
            "This report is evidence assistance, not a release approval.",
            "Offline deterministic extraction is the default; semantic model review is optional.",
            *state.get("warnings", []),
            *decision.reasons,
        ]
        route_value = state.get("route", "single")
        safe_route: Literal["single", "multi"] = "multi" if route_value == "multi" else "single"
        report = ReleaseAssessment(
            run_id=state["run_id"],
            change_summary=summary,
            change_profile=profile,
            acceptance_matrix=results,
            domain_risks=risks,
            missing_evidence=missing,
            human_checks=human_checks,
            rollback_notes=rollback_notes,
            recommendation=decision.recommendation,
            evidence_index=[item.as_ref() for item in evidence],
            limitations=list(dict.fromkeys(limitations)),
            active_skills=state.get("active_skills", []),
            route=safe_route,
            stop_reason=state.get("stop_reason", "completed"),
            prompt_versions=list(dict.fromkeys(state.get("prompt_versions", []))),
            llm_usage=state.get("llm_usage", {}),
        )
        state["report"] = report.model_dump(mode="json")
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "policy_gate_and_report",
            f"Deterministic policy gate returned {decision.recommendation.value}; human review remains required.",
        )
        return state

    def complete_without_interrupt(self, state: WorkflowState) -> WorkflowState:
        for node in (
            self.load_relevant_skills,
            self.route_analysis,
            self.build_acceptance_matrix,
            self.write_report,
        ):
            state = node(state)
        return state

    def run_manual(self, initial_state: WorkflowState) -> tuple[WorkflowState, InterruptPayload | None]:
        state = cast(WorkflowState, initial_state.copy())
        for node in (
            self.validate_request,
            self.collect_change_facts,
            self.extract_acceptance_criteria,
            self.profile_change,
        ):
            state = node(state)
        reasons = self.missing_context_reasons(state)
        request = AnalysisRequest.model_validate(state["request"])
        if reasons and not request.continue_without_reports:
            payload = InterruptPayload(
                run_id=state["run_id"],
                reasons=reasons,
                requested_inputs=[
                    "machine-readable JUnit/coverage/CI report inside the repository, or",
                    "an explicit choice to continue with an incomplete evidence report",
                ],
            )
            state["trace"] = self._trace(state, "request_missing_context", "Paused for concrete missing evidence.", status="paused")
            return state, payload
        return self.complete_without_interrupt(state), None


def build_langgraph(nodes: WorkflowNodes, checkpoint_path: Path):
    """Compile the durable workflow using LangGraph and a local SQLite checkpointer."""

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as exc:  # pragma: no cover - dependency gate
        raise RuntimeError("LangGraph SQLite dependencies are not installed") from exc

    def request_missing_context(state: WorkflowState) -> WorkflowState:
        reasons = nodes.missing_context_reasons(state)
        request = AnalysisRequest.model_validate(state["request"])
        if not reasons or request.continue_without_reports:
            return state
        answer = interrupt(
            InterruptPayload(
                run_id=state["run_id"],
                reasons=reasons,
                requested_inputs=[
                    "machine-readable JUnit/coverage/CI report inside the repository, or",
                    "an explicit choice to continue with an incomplete evidence report",
                ],
            ).model_dump(mode="json")
        )
        resumed = nodes.apply_resume(state, ResumeRequest.model_validate(answer))
        resumed = nodes.refresh_after_resume(resumed)
        resumed["trace"] = nodes._trace(
            resumed,
            "request_missing_context",
            "Resumed with bounded human input; pre-interrupt collection remains idempotent.",
        )
        return resumed

    def context_route(state: WorkflowState) -> str:
        request = AnalysisRequest.model_validate(state["request"])
        if nodes.missing_context_reasons(state) and not request.continue_without_reports:
            return "retry"
        return "continue"

    builder = StateGraph(WorkflowState)
    builder.add_node("validate_request", nodes.validate_request)
    builder.add_node("collect_change_facts", nodes.collect_change_facts)
    builder.add_node("extract_acceptance_criteria", nodes.extract_acceptance_criteria)
    builder.add_node("profile_change", nodes.profile_change)
    builder.add_node("request_missing_context", request_missing_context)
    builder.add_node("load_relevant_skills", nodes.load_relevant_skills)
    builder.add_node("route_analysis", nodes.route_analysis)
    builder.add_node("build_acceptance_matrix", nodes.build_acceptance_matrix)
    builder.add_node("write_report", nodes.write_report)
    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "collect_change_facts")
    builder.add_edge("collect_change_facts", "extract_acceptance_criteria")
    builder.add_edge("extract_acceptance_criteria", "profile_change")
    builder.add_edge("profile_change", "request_missing_context")
    builder.add_conditional_edges(
        "request_missing_context",
        context_route,
        {"retry": "request_missing_context", "continue": "load_relevant_skills"},
    )
    builder.add_edge("load_relevant_skills", "route_analysis")
    builder.add_edge("route_analysis", "build_acceptance_matrix")
    builder.add_edge("build_acceptance_matrix", "write_report")
    builder.add_edge("write_report", END)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    graph = builder.compile(checkpointer=checkpointer)
    return graph, connection

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, cast

from pydantic import ValidationError

from release_proof.adapters.llm import StructuredOutputError
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
    NextAction,
    Recommendation,
    ReleaseAssessment,
    ResumeRequest,
    RiskDomain,
    TraceEvent,
)
from release_proof.domain.policy import ReleasePolicyGate
from release_proof.evidence.ledger import EvidenceLedger
from release_proof.evidence.validator import EvidenceValidator
from release_proof.graph.harness import EvidenceToolHarness, ToolHarnessRejection
from release_proof.graph.matrix import AcceptanceMatrixBuilder
from release_proof.graph.planner import (
    DeterministicEvidencePlanner,
    ModelEvidencePlanner,
    PlannerOutcome,
)
from release_proof.graph.profiler import choose_route, profile_change
from release_proof.graph.skills import SkillLoader
from release_proof.graph.specialists import SpecialistCoordinator
from release_proof.requirements.extractor import (
    DeterministicAcceptanceExtractor,
    LLMAcceptanceExtractor,
    StructuredLLM,
)
from release_proof.tools.registry import ReadOnlyToolRegistry


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
    llm_degraded: NotRequired[bool]
    agent_steps_used: NotRequired[int]
    no_progress_count: NotRequired[int]
    seen_action_keys: NotRequired[list[str]]
    action_history: NotRequired[list[dict[str, Any]]]
    criterion_gaps: NotRequired[list[dict[str, Any]]]
    pending_action: NotRequired[dict[str, Any]]
    pending_tool_observation: NotRequired[dict[str, Any]]
    pending_evidence: NotRequired[list[dict[str, Any]]]
    deadline_at: NotRequired[str]
    paused_at: NotRequired[str | None]
    paused_seconds_total: NotRequired[float]
    recoverable_tool_error: NotRequired[dict[str, Any] | None]
    change_summary: NotRequired[dict[str, Any]]
    requirement_text: NotRequired[str]
    requirement_evidence: NotRequired[dict[str, Any]]
    evidence: NotRequired[list[dict[str, Any]]]
    tool_observations: NotRequired[list[dict[str, Any]]]
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


def _safe_model_failure(exc: Exception) -> str:
    """Expose adapter-owned diagnostics without persisting provider content or prompts."""
    if isinstance(exc, StructuredOutputError):
        return f"{type(exc).__name__}: {str(exc)[:240]}"
    return type(exc).__name__


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
        self.harness = EvidenceToolHarness()
        self.offline_extractor = DeterministicAcceptanceExtractor()
        self.offline_planner = DeterministicEvidencePlanner()
        self.llm = llm
        self.planner = (
            ModelEvidencePlanner(llm, max_output_tokens=min(max_output_tokens, 900))
            if llm is not None
            else self.offline_planner
        )
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
    def _record_failed_model_usage(state: WorkflowState, exc: Exception) -> None:
        usage = exc.usage if isinstance(exc, StructuredOutputError) else {}
        observed = False
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                observed = True
                state["llm_usage"][key] = state.get("llm_usage", {}).get(key, 0) + value
        if not observed:
            state["llm_usage"]["unknown_usage_calls"] = (
                state.get("llm_usage", {}).get("unknown_usage_calls", 0) + 1
            )

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
        evidence_ids: list[str] | None = None,
        tool: str | None = None,
        duration_ms: int | None = None,
    ) -> list[dict]:
        step = int(state.get("step_count", 0)) + 1
        event = TraceEvent(
            step=step,
            node=node,
            status=status,
            summary=summary,
            evidence_ids=evidence_ids or [],
            tool=tool,
            duration_ms=duration_ms,
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

    def bootstrap_change_facts(self, state: WorkflowState) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        facts = self.harness.bootstrap(request)
        state.update(
            {
                "change_summary": facts.summary.model_dump(mode="json"),
                "requirement_text": facts.requirement_text,
                "requirement_evidence": facts.requirement_evidence.model_dump(mode="json"),
                "evidence": [facts.requirement_evidence.model_dump(mode="json")],
                "tool_count": len(facts.observations),
                "tool_observations": facts.observations,
                "seen_action_keys": facts.seen_action_keys,
                "agent_steps_used": int(state.get("agent_steps_used", 0)),
                "no_progress_count": int(state.get("no_progress_count", 0)),
                "action_history": list(state.get("action_history", [])),
            }
        )
        if len(facts.observations) >= request.limits.max_tool_calls:
            state["budget_exhausted"] = True
            state["stop_reason"] = "tool_call_limit"
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "bootstrap_change_facts",
            "Bootstrapped only the immutable change manifest and requirement source; "
            "business evidence remains planner-selected.",
            usage={
                "tool_calls": len(facts.observations),
                "tool_errors": sum(item["status"] == "error" for item in facts.observations),
                "tool_chain": ",".join(
                    f"{item['name']}:{item['call_key']}:{item['status']}"
                    for item in facts.observations
                ),
            },
            evidence_ids=[facts.requirement_evidence.id],
            tool="deterministic_bootstrap",
            duration_ms=sum(int(item.get("duration_ms") or 0) for item in facts.observations),
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
                if self.llm is not None:
                    state["llm_degraded"] = True
                    self._record_failed_model_usage(state, exc)
                outcome = self.offline_extractor.extract_outcome(
                    _required(state, "requirement_text"), source
                )
                state["warnings"] = [
                    *state.get("warnings", []),
                    f"online acceptance extraction failed ({_safe_model_failure(exc)}); "
                    "offline baseline used",
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

    def compute_evidence_gaps(self, state: WorkflowState) -> WorkflowState:
        criteria = _model_list(AcceptanceCriterion, _required(state, "criteria"))
        evidence = _model_list(EvidenceItem, state.get("evidence", []))
        interim = self.matrix.build(criteria, evidence)
        gaps: list[dict[str, Any]] = []
        for result in interim:
            missing_layers: list[str] = []
            if not result.implementation_evidence:
                missing_layers.append("implementation")
            if not result.verification_evidence:
                missing_layers.append("verification")
            gaps.append(
                {
                    "criterion_id": result.criterion_id,
                    "status": result.status.value,
                    "missing_layers": missing_layers,
                    "missing_evidence": result.missing_evidence,
                }
            )
        state["criterion_gaps"] = gaps
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "compute_evidence_gaps",
            f"Recomputed bounded evidence gaps for {len(criteria)} criterion/criteria.",
            evidence_ids=[item.id for item in evidence],
        )
        return state

    def _deadline_expired(self, state: WorkflowState, request: AnalysisRequest) -> bool:
        raw = state.get("deadline_at")
        if not raw:
            state["deadline_at"] = (
                datetime.now(UTC) + timedelta(seconds=request.limits.max_elapsed_seconds)
            ).isoformat()
            return False
        return datetime.now(UTC) >= datetime.fromisoformat(raw)

    @staticmethod
    def _finished_outcome(reason: str, model: str = "deterministic-guard") -> PlannerOutcome:
        return PlannerOutcome(
            action=NextAction(action="finish", reason=reason),
            prompt_version="planner-guard-v1",
            model=model,
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    @staticmethod
    def _request_input_outcome(
        reason: str,
        requested_inputs: list[str],
    ) -> PlannerOutcome:
        return PlannerOutcome(
            action=NextAction(
                action="request_input",
                reason=reason,
                requested_inputs=requested_inputs,
            ),
            prompt_version="planner-guard-v1",
            model="deterministic-guard",
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    def choose_next_action(self, state: WorkflowState) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        summary = ChangeSummary.model_validate(_required(state, "change_summary"))
        criteria = _model_list(AcceptanceCriterion, _required(state, "criteria"))
        evidence = _model_list(EvidenceItem, state.get("evidence", []))
        used_steps = int(state.get("agent_steps_used", 0))
        tool_count = int(state.get("tool_count", 0))
        prior_stop = state.get("stop_reason", "completed")
        recoverable_error = state.get("recoverable_tool_error")
        if recoverable_error:
            outcome = self._request_input_outcome(
                str(
                    recoverable_error.get(
                        "reason",
                        "A declared input could not be consumed safely.",
                    )
                ),
                [
                    str(item)
                    for item in recoverable_error.get(
                        "requested_inputs",
                        ["a corrected repository-local report or CI snapshot"],
                    )
                ],
            )
        elif prior_stop != "completed":
            outcome = self._finished_outcome(
                f"Stop because the deterministic harness set {prior_stop}."
            )
        elif self._deadline_expired(state, request):
            state["budget_exhausted"] = True
            state["stop_reason"] = "elapsed_time_limit"
            outcome = self._finished_outcome("The persistent wall-clock deadline was reached.")
        elif used_steps >= request.limits.max_steps:
            state["budget_exhausted"] = True
            state["stop_reason"] = "step_limit"
            outcome = self._finished_outcome("The configured planner step limit was reached.")
        elif tool_count >= request.limits.max_tool_calls:
            state["budget_exhausted"] = True
            state["stop_reason"] = "tool_call_limit"
            outcome = self._finished_outcome("The configured tool-call limit was reached.")
        elif (
            self.llm is not None
            and not state.get("llm_degraded", False)
            and int(state.get("llm_call_count", 0)) >= self.max_llm_calls
        ):
            state["budget_exhausted"] = True
            state["stop_reason"] = "llm_call_limit"
            outcome = self._finished_outcome("The configured model-call limit was reached.")
        else:
            planner = self.offline_planner if state.get("llm_degraded", False) else self.planner
            if self.llm is not None and not state.get("llm_degraded", False):
                state["llm_call_count"] = int(state.get("llm_call_count", 0)) + 1
            try:
                outcome = planner.choose(
                    request=request,
                    summary=summary,
                    criteria=criteria,
                    criterion_gaps=state.get("criterion_gaps", []),
                    evidence=evidence,
                    action_history=state.get("action_history", []),
                    active_skill_context=state.get("active_skill_context", []),
                    remaining_steps=request.limits.max_steps - used_steps,
                    remaining_tool_calls=request.limits.max_tool_calls - tool_count,
                )
            except Exception as exc:
                state["llm_degraded"] = True
                self._record_failed_model_usage(state, exc)
                state["warnings"] = [
                    *state.get("warnings", []),
                    f"online planner failed ({_safe_model_failure(exc)}); the run-scoped circuit "
                    "breaker switched subsequent decisions to the bounded deterministic planner",
                ]
                outcome = self.offline_planner.choose(
                    request=request,
                    summary=summary,
                    criteria=criteria,
                    criterion_gaps=state.get("criterion_gaps", []),
                    evidence=evidence,
                    action_history=state.get("action_history", []),
                    active_skill_context=state.get("active_skill_context", []),
                    remaining_steps=request.limits.max_steps - used_steps,
                    remaining_tool_calls=request.limits.max_tool_calls - tool_count,
                )
        action = outcome.action
        state["agent_steps_used"] = used_steps + 1
        state["pending_action"] = action.model_dump(mode="json")
        state["action_history"] = [
            *state.get("action_history", []),
            {
                **action.model_dump(mode="json"),
                "planner": outcome.model,
                "prompt_version": outcome.prompt_version,
                "status": "finished" if action.action == "finish" else "proposed",
            },
        ]
        state["prompt_versions"] = list(
            dict.fromkeys([*state.get("prompt_versions", []), outcome.prompt_version])
        )
        for key in ("input_tokens", "output_tokens"):
            value = outcome.usage.get(key, 0)
            if isinstance(value, (int, float)):
                state["llm_usage"][key] = state.get("llm_usage", {}).get(key, 0) + value
        state["llm_usage"]["calls"] = int(state.get("llm_call_count", 0))
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "choose_next_action",
            f"Planner selected {action.action}: {action.reason}",
            prompt_version=outcome.prompt_version,
            model=outcome.model,
            usage={
                key: value
                for key, value in outcome.usage.items()
                if isinstance(value, (int, float, str))
            },
            skills=state.get("active_skills", []),
            tool=action.tool_name,
        )
        return state

    def validate_and_execute_tool(self, state: WorkflowState) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        summary = ChangeSummary.model_validate(_required(state, "change_summary"))
        criteria = _model_list(AcceptanceCriterion, _required(state, "criteria"))
        evidence = _model_list(EvidenceItem, state.get("evidence", []))
        action = NextAction.model_validate(_required(state, "pending_action"))
        status: Literal["completed", "failed"] = "completed"
        summary_text = ""
        evidence_ids: list[str] = []
        duration_ms = 0
        observation_payload: dict[str, Any]
        try:
            if self._deadline_expired(state, request):
                state["budget_exhausted"] = True
                state["stop_reason"] = "elapsed_time_limit"
                raise ToolHarnessRejection("persistent wall-clock deadline reached")
            if int(state.get("tool_count", 0)) >= request.limits.max_tool_calls:
                raise ToolHarnessRejection("tool-call budget reached")
            call = self.harness.build_call(
                action=action,
                request=request,
                summary=summary,
            )
            action_key = ReadOnlyToolRegistry.action_key(call)
            if action_key in set(state.get("seen_action_keys", [])):
                state["stop_reason"] = "duplicate_tool_action"
                raise ToolHarnessRejection("duplicate stable action key")
            result = self.harness.execute(
                request=request,
                summary=summary,
                criteria=criteria,
                action=action,
                existing_evidence=evidence,
            )
            observation = result.observation
            state["tool_count"] = int(state.get("tool_count", 0)) + 1
            state["seen_action_keys"] = [
                *state.get("seen_action_keys", []),
                observation.call_key,
            ]
            state["pending_evidence"] = [item.model_dump(mode="json") for item in result.evidence]
            observation_payload = {
                "name": observation.name,
                "call_key": observation.call_key,
                "status": observation.status,
                "error_category": observation.error_category,
                "duration_ms": observation.duration_ms,
                "planner_selected": True,
            }
            state["pending_tool_observation"] = observation_payload
            state["tool_observations"] = [
                *state.get("tool_observations", []),
                observation_payload,
            ]
            evidence_ids = [item.id for item in result.evidence]
            duration_ms = observation.duration_ms
            added = result.added_evidence
            state["no_progress_count"] = (
                0 if added > 0 else int(state.get("no_progress_count", 0)) + 1
            )
            if observation.status == "error":
                summary_text = (
                    f"Planner-selected {observation.name} returned "
                    f"{observation.error_category or 'tool_error'}."
                )
            else:
                summary_text = (
                    f"Executed planner-selected {observation.name}; "
                    f"{added} new evidence item(s) await ledger ingest."
                )
            if int(state.get("no_progress_count", 0)) >= request.limits.max_no_progress:
                state["budget_exhausted"] = True
                state["stop_reason"] = "no_progress_limit"
        except (ToolHarnessRejection, ValidationError, ValueError) as exc:
            status = "failed"
            recoverable = isinstance(exc, ToolHarnessRejection) and exc.recoverable
            state["no_progress_count"] = int(state.get("no_progress_count", 0)) + 1
            if recoverable:
                state["warnings"] = [
                    *state.get("warnings", []),
                    "A declared input could not be consumed; the run can request a replacement.",
                ]
                state["recoverable_tool_error"] = {
                    "reason": "A declared report or CI input could not be consumed safely.",
                    "requested_inputs": ["a corrected repository-local report or CI snapshot"],
                    "tool_name": action.tool_name,
                    "path": str(action.arguments.get("path", "")),
                }
            else:
                state["budget_exhausted"] = True
                if state.get("stop_reason", "completed") == "completed":
                    state["stop_reason"] = "tool_policy_rejected"
            if (
                int(state.get("no_progress_count", 0)) >= request.limits.max_no_progress
                and state.get("stop_reason", "completed") == "completed"
            ):
                state["budget_exhausted"] = True
                state["stop_reason"] = "no_progress_limit"
            state["pending_evidence"] = []
            observation_payload = {
                "name": action.tool_name or "",
                "call_key": "",
                "status": "rejected",
                "error_category": type(exc).__name__,
                "duration_ms": 0,
                "planner_selected": True,
            }
            state["pending_tool_observation"] = observation_payload
            state["tool_observations"] = [
                *state.get("tool_observations", []),
                observation_payload,
            ]
            summary_text = f"Rejected planner-selected tool action: {type(exc).__name__}."
        history = list(state.get("action_history", []))
        if history:
            history[-1] = {
                **history[-1],
                "status": "executed" if status == "completed" else "rejected",
                "stop_reason": state.get("stop_reason", "completed"),
            }
            state["action_history"] = history
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "validate_and_execute_readonly_tool",
            summary_text,
            status=status,
            evidence_ids=evidence_ids,
            tool=action.tool_name,
            duration_ms=duration_ms,
            usage={
                "tool_calls": int(state.get("tool_count", 0)),
                "no_progress": int(state.get("no_progress_count", 0)),
                "planner_selected": "true",
            },
        )
        return state

    def ingest_evidence(self, state: WorkflowState) -> WorkflowState:
        ledger = EvidenceLedger(_model_list(EvidenceItem, state.get("evidence", [])))
        pending = _model_list(EvidenceItem, state.get("pending_evidence", []))
        added = ledger.extend(pending)
        state["evidence"] = [item.model_dump(mode="json") for item in ledger.items()]
        state["pending_evidence"] = []
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "ingest_evidence",
            f"Committed {added} new immutable evidence item(s) to the run ledger.",
            evidence_ids=[item.id for item in pending],
            tool=(
                str(state.get("pending_tool_observation", {}).get("name"))
                if state.get("pending_tool_observation")
                else None
            ),
        )
        state["pending_tool_observation"] = {}
        state["pending_action"] = {}
        return state

    def pause_for_input(self, state: WorkflowState) -> WorkflowState:
        action = NextAction.model_validate(_required(state, "pending_action"))
        if action.action != "request_input":
            raise ValueError("only request_input can enter the pause node")
        if not state.get("paused_at"):
            state["paused_at"] = datetime.now(UTC).isoformat()
        history = list(state.get("action_history", []))
        if history:
            history[-1] = {**history[-1], "status": "paused"}
            state["action_history"] = history
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "request_missing_context",
            action.reason,
            status="paused",
        )
        return state

    def interrupt_payload(self, state: WorkflowState) -> InterruptPayload:
        action = NextAction.model_validate(_required(state, "pending_action"))
        if action.action != "request_input":
            raise ValueError("pending action is not request_input")
        return InterruptPayload(
            run_id=state["run_id"],
            reasons=[action.reason],
            requested_inputs=action.requested_inputs,
        )

    @staticmethod
    def _report_path_key(
        repository_path: str,
        value: str,
    ) -> str:
        root = Path(repository_path).resolve()
        try:
            raw = Path(value)
            resolved = (
                raw.resolve(strict=False)
                if raw.is_absolute()
                else (root / raw).resolve(strict=False)
            )
            return str(resolved).casefold()
        except (OSError, ValueError):
            return value.replace("\\", "/").casefold()

    @classmethod
    def _deduplicate_report_paths(
        cls,
        repository_path: str,
        paths: list[str],
    ) -> list[str]:
        deduplicated: list[str] = []
        seen: set[str] = set()
        for value in paths:
            key = cls._report_path_key(repository_path, value)
            if key not in seen:
                seen.add(key)
                deduplicated.append(value)
        return deduplicated

    def apply_resume(self, state: WorkflowState, resume: ResumeRequest) -> WorkflowState:
        request = AnalysisRequest.model_validate(state["request"])
        recoverable_error = state.get("recoverable_tool_error")
        now = datetime.now(UTC)
        paused_at = state.get("paused_at")
        if paused_at:
            pause_started = datetime.fromisoformat(paused_at)
            paused_seconds = max(0.0, (now - pause_started).total_seconds())
            deadline_at = state.get("deadline_at")
            if deadline_at:
                state["deadline_at"] = (
                    datetime.fromisoformat(deadline_at) + timedelta(seconds=paused_seconds)
                ).isoformat()
            state["paused_seconds_total"] = (
                float(state.get("paused_seconds_total", 0.0)) + paused_seconds
            )
        # LangGraph checkpoint channels merge node updates. Omitting a key does
        # not delete its previous value, so clearing resumable state must be an
        # explicit update rather than ``pop``.
        state["paused_at"] = None
        existing_report_paths = list(request.report_paths)
        resumed_report_paths = list(resume.report_paths)
        rejected_path = ""
        rejected_tool = ""
        if recoverable_error:
            rejected_path = str(recoverable_error.get("path", ""))
            rejected_tool = str(recoverable_error.get("tool_name", ""))
        if (
            rejected_tool == "read_test_report"
            and rejected_path
            and (resumed_report_paths or resume.continue_without_reports)
        ):
            rejected_key = self._report_path_key(request.repository_path, rejected_path)
            existing_report_paths = [
                value
                for value in existing_report_paths
                if self._report_path_key(request.repository_path, value) != rejected_key
            ]
            resumed_report_paths = [
                (
                    rejected_path
                    if self._report_path_key(request.repository_path, value) == rejected_key
                    else value
                )
                for value in resumed_report_paths
            ]
        report_paths = self._deduplicate_report_paths(
            request.repository_path,
            [*existing_report_paths, *resumed_report_paths],
        )
        ci_snapshot_path = resume.ci_snapshot_path or request.ci_snapshot_path
        if (
            rejected_tool == "read_ci_summary"
            and rejected_path
            and resume.continue_without_reports
            and not resume.ci_snapshot_path
        ):
            ci_snapshot_path = None
        merged = request.model_copy(
            update={
                "report_paths": report_paths,
                "ci_snapshot_path": ci_snapshot_path,
                "continue_without_reports": resume.continue_without_reports,
            }
        )
        state["request"] = merged.model_dump(mode="json")
        state["resume_payload"] = resume.model_dump(mode="json")
        if resume.clarifications:
            ledger = EvidenceLedger(_model_list(EvidenceItem, state.get("evidence", [])))
            for question, answer in sorted(resume.clarifications.items()):
                stable = hashlib.sha256(f"{question}\0{answer}".encode()).hexdigest()[:16]
                ledger.add(
                    EvidenceItem.from_observation(
                        evidence_id=f"human-{stable}",
                        kind=EvidenceKind.HUMAN_INPUT,
                        source_uri="human://resume",
                        locator=question,
                        content=answer,
                        observed_by="human_input:v1",
                    )
                )
            state["evidence"] = [item.model_dump(mode="json") for item in ledger.items()]
        state["pending_action"] = {}
        if resume.report_paths or resume.ci_snapshot_path or resume.continue_without_reports:
            state["recoverable_tool_error"] = None
        state["resume_payload"] = resume.model_dump(mode="json")
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
        candidates = (
            []
            if state.get("llm_degraded", False)
            else self.specialists.llm_candidate_domains(profile.risk_domains, evidence)
        )
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
        if int(state.get("agent_steps_used", 0)) > request.limits.max_steps:
            state["budget_exhausted"] = True
            state["stop_reason"] = "step_limit"
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
            {item for result in results for item in result.missing_evidence}
            | {item for report in domain_reports for item in report.missing_evidence}
        )
        risks = [risk for report in domain_reports for risk in report.risks]
        human_checks = [
            HumanCheck(
                id=f"HC-{index:03d}",
                question=missing_item,
                reason="The available evidence cannot resolve this check automatically.",
                blocking=decision.recommendation != Recommendation.READY_FOR_HUMAN_REVIEW,
            )
            for index, missing_item in enumerate(missing, start=1)
        ]
        rollback_notes: list[str] = []
        if RiskDomain.DATA_MIGRATION in profile.risk_domains:
            rollback_notes.append(
                "Have a reviewed rollback or forward-fix plan for data migration changes."
            )
        if RiskDomain.CONFIG_DEPLOYMENT in profile.risk_domains:
            rollback_notes.append(
                "Record the previous configuration and a bounded rollback trigger."
            )
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
            self.route_analysis,
            self.build_acceptance_matrix,
            self.write_report,
        ):
            state = node(state)
        return state

    def run_manual(
        self, initial_state: WorkflowState
    ) -> tuple[WorkflowState, InterruptPayload | None]:
        state = cast(WorkflowState, initial_state.copy())
        for node in (
            self.validate_request,
            self.bootstrap_change_facts,
            self.extract_acceptance_criteria,
            self.profile_change,
            self.load_relevant_skills,
            self.compute_evidence_gaps,
        ):
            state = node(state)
        return self._run_manual_agent_loop(state)

    def resume_manual(
        self,
        state: WorkflowState,
        resume: ResumeRequest,
    ) -> tuple[WorkflowState, InterruptPayload | None]:
        state = self.apply_resume(state, resume)
        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["trace"] = self._trace(
            {**state, "step_count": state["step_count"] - 1},
            "request_missing_context",
            "Resumed with bounded human input; existing evidence and budgets were preserved.",
        )
        state = self.compute_evidence_gaps(state)
        return self._run_manual_agent_loop(state)

    def _run_manual_agent_loop(
        self,
        state: WorkflowState,
    ) -> tuple[WorkflowState, InterruptPayload | None]:
        while True:
            state = self.choose_next_action(state)
            action = NextAction.model_validate(_required(state, "pending_action"))
            if action.action == "call_tool":
                state = self.validate_and_execute_tool(state)
                state = self.ingest_evidence(state)
                state = self.compute_evidence_gaps(state)
                continue
            if action.action == "request_input":
                state = self.pause_for_input(state)
                return state, self.interrupt_payload(state)
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
        payload = nodes.interrupt_payload(state)
        answer = interrupt(payload.model_dump(mode="json"))
        resumed = nodes.apply_resume(state, ResumeRequest.model_validate(answer))
        resumed["step_count"] = int(resumed.get("step_count", 0)) + 1
        resumed["trace"] = nodes._trace(
            {**resumed, "step_count": resumed["step_count"] - 1},
            "request_missing_context",
            "Resumed with bounded human input; evidence, action keys, and budgets were preserved.",
        )
        return resumed

    def action_route(state: WorkflowState) -> str:
        action = NextAction.model_validate(_required(state, "pending_action"))
        return action.action

    builder = StateGraph(WorkflowState)
    builder.add_node("validate_request", nodes.validate_request)
    builder.add_node("bootstrap_change_facts", nodes.bootstrap_change_facts)
    builder.add_node("extract_acceptance_criteria", nodes.extract_acceptance_criteria)
    builder.add_node("profile_change", nodes.profile_change)
    builder.add_node("load_relevant_skills", nodes.load_relevant_skills)
    builder.add_node("compute_evidence_gaps", nodes.compute_evidence_gaps)
    builder.add_node("choose_next_action", nodes.choose_next_action)
    builder.add_node("validate_and_execute_readonly_tool", nodes.validate_and_execute_tool)
    builder.add_node("ingest_evidence", nodes.ingest_evidence)
    builder.add_node("pause_for_input", nodes.pause_for_input)
    builder.add_node("request_missing_context", request_missing_context)
    builder.add_node("route_analysis", nodes.route_analysis)
    builder.add_node("build_acceptance_matrix", nodes.build_acceptance_matrix)
    builder.add_node("write_report", nodes.write_report)
    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "bootstrap_change_facts")
    builder.add_edge("bootstrap_change_facts", "extract_acceptance_criteria")
    builder.add_edge("extract_acceptance_criteria", "profile_change")
    builder.add_edge("profile_change", "load_relevant_skills")
    builder.add_edge("load_relevant_skills", "compute_evidence_gaps")
    builder.add_edge("compute_evidence_gaps", "choose_next_action")
    builder.add_conditional_edges(
        "choose_next_action",
        action_route,
        {
            "call_tool": "validate_and_execute_readonly_tool",
            "request_input": "pause_for_input",
            "finish": "route_analysis",
        },
    )
    builder.add_edge("validate_and_execute_readonly_tool", "ingest_evidence")
    builder.add_edge("ingest_evidence", "compute_evidence_gaps")
    builder.add_edge("pause_for_input", "request_missing_context")
    builder.add_edge("request_missing_context", "compute_evidence_gaps")
    builder.add_edge("route_analysis", "build_acceptance_matrix")
    builder.add_edge("build_acceptance_matrix", "write_report")
    builder.add_edge("write_report", END)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    graph = builder.compile(checkpointer=checkpointer)
    return graph, connection

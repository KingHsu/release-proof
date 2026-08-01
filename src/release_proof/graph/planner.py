from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from release_proof.domain.models import (
    AcceptanceCriterion,
    AnalysisRequest,
    ChangeSummary,
    EvidenceItem,
    EvidenceKind,
    NextAction,
)
from release_proof.prompts import get_prompt
from release_proof.requirements.extractor import StructuredLLM
from release_proof.tools.registry import ARGUMENT_MODELS

CORE_AGENT_TOOLS = (
    "read_diff",
    "read_file",
    "search_code",
    "read_test_report",
    "read_ci_summary",
)


@dataclass(frozen=True)
class PlannerOutcome:
    action: NextAction
    prompt_version: str
    model: str
    usage: dict[str, Any]


class EvidencePlanner(Protocol):
    def choose(
        self,
        *,
        request: AnalysisRequest,
        summary: ChangeSummary,
        criteria: list[AcceptanceCriterion],
        criterion_gaps: list[dict[str, Any]],
        evidence: list[EvidenceItem],
        action_history: list[dict[str, Any]],
        active_skill_context: list[dict[str, Any]],
        remaining_steps: int,
        remaining_tool_calls: int,
    ) -> PlannerOutcome: ...


class ModelEvidencePlanner:
    """Structured model planner whose proposals are still code-policy constrained."""

    def __init__(self, llm: StructuredLLM, *, max_output_tokens: int = 900) -> None:
        self.llm = llm
        self.max_output_tokens = max_output_tokens
        self.prompt = get_prompt("choose_next_evidence_action")

    def choose(
        self,
        *,
        request: AnalysisRequest,
        summary: ChangeSummary,
        criteria: list[AcceptanceCriterion],
        criterion_gaps: list[dict[str, Any]],
        evidence: list[EvidenceItem],
        action_history: list[dict[str, Any]],
        active_skill_context: list[dict[str, Any]],
        remaining_steps: int,
        remaining_tool_calls: int,
    ) -> PlannerOutcome:
        context = {
            "repository": {
                "base_ref": summary.base_ref,
                "head_ref": summary.head_ref,
                "changed_files": summary.changed_files,
            },
            "criteria": [
                {
                    "id": item.id,
                    "statement": item.statement,
                    "critical": item.critical,
                    "ambiguity": item.ambiguity,
                }
                for item in criteria
            ],
            "criterion_gaps": criterion_gaps,
            "available_reports": request.report_paths,
            "available_ci_snapshot": request.ci_snapshot_path,
            "evidence": [
                {
                    "id": item.id,
                    "kind": item.kind.value,
                    "locator": item.locator,
                    "excerpt": item.content_excerpt[:600],
                }
                for item in evidence[-30:]
            ],
            "previous_actions": action_history[-20:],
            "active_skills": [
                {
                    "name": item.get("name"),
                    "version": item.get("version"),
                    "instructions": str(item.get("instructions", ""))[:2400],
                }
                for item in active_skill_context
            ],
            "allowed_tools": {
                name: ARGUMENT_MODELS[name].model_json_schema()
                for name in CORE_AGENT_TOOLS
            },
            "remaining_budget": {
                "steps": remaining_steps,
                "tool_calls": remaining_tool_calls,
            },
        }
        parsed, usage = self.llm.structured(
            system=self.prompt.system,
            user=self.prompt.task_template.format(
                context=json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            ),
            schema=NextAction,
            max_tokens=self.max_output_tokens,
        )
        return PlannerOutcome(
            action=NextAction.model_validate(parsed),
            prompt_version=self.prompt.identifier,
            model=self.llm.model,
            usage=usage,
        )


class DeterministicEvidencePlanner:
    """Offline baseline that drives the same action/harness loop without an API."""

    version = "offline-evidence-planner-v1"

    @staticmethod
    def _was_called(
        history: list[dict[str, Any]], tool_name: str, arguments: dict[str, Any]
    ) -> bool:
        return any(
            item.get("action") == "call_tool"
            and item.get("tool_name") == tool_name
            and item.get("arguments") == arguments
            and item.get("status") == "executed"
            for item in history
        )

    def choose(
        self,
        *,
        request: AnalysisRequest,
        summary: ChangeSummary,
        criteria: list[AcceptanceCriterion],
        criterion_gaps: list[dict[str, Any]],
        evidence: list[EvidenceItem],
        action_history: list[dict[str, Any]],
        active_skill_context: list[dict[str, Any]],
        remaining_steps: int,
        remaining_tool_calls: int,
    ) -> PlannerOutcome:
        del active_skill_context
        criterion_ids = [item.id for item in criteria]
        if remaining_steps <= 0 or remaining_tool_calls <= 0:
            action = NextAction(
                action="finish",
                reason="The configured agent or tool budget has no remaining capacity.",
            )
            return self._outcome(action)

        for path in summary.changed_files:
            arguments = {
                "base_ref": summary.base_ref,
                "head_ref": summary.head_ref,
                "path": path,
            }
            if not self._was_called(action_history, "read_diff", arguments):
                return self._outcome(
                    NextAction(
                        action="call_tool",
                        tool_name="read_diff",
                        arguments=arguments,
                        target_criterion_ids=criterion_ids,
                        expected_evidence_kind=EvidenceKind.DIFF,
                        reason=f"Read the bounded change hunk for {path}.",
                    )
                )

        for index, path in enumerate(request.report_paths, start=1):
            arguments = {"path": path, "evidence_prefix": f"planned-report-{index}"}
            if not self._was_called(action_history, "read_test_report", arguments):
                return self._outcome(
                    NextAction(
                        action="call_tool",
                        tool_name="read_test_report",
                        arguments=arguments,
                        target_criterion_ids=criterion_ids,
                        expected_evidence_kind=EvidenceKind.TEST_RESULT,
                        reason="Consume a declared machine-readable test or coverage report.",
                    )
                )

        if request.ci_snapshot_path:
            arguments = {
                "path": request.ci_snapshot_path,
                "evidence_prefix": "planned-ci",
            }
            if not self._was_called(action_history, "read_ci_summary", arguments):
                return self._outcome(
                    NextAction(
                        action="call_tool",
                        tool_name="read_ci_summary",
                        arguments=arguments,
                        target_criterion_ids=criterion_ids,
                        expected_evidence_kind=EvidenceKind.CI,
                        reason="Consume the declared immutable CI snapshot.",
                    )
                )

        has_human_input = any(item.kind == EvidenceKind.HUMAN_INPUT for item in evidence)
        ambiguous = any(item.ambiguity for item in criteria)
        verification_missing = any(
            "verification" in item.get("missing_layers", []) for item in criterion_gaps
        )
        if (ambiguous and not has_human_input) or (
            request.require_verification_evidence
            and verification_missing
            and not request.continue_without_reports
        ):
            requested = []
            if ambiguous and not has_human_input:
                requested.append("clarification for the ambiguous acceptance criterion")
            if verification_missing:
                requested.append(
                    "a machine-readable JUnit, coverage, or CI report inside the repository"
                )
            reason = (
                "no machine-readable test, coverage, or CI evidence was supplied"
                if verification_missing
                else "an acceptance criterion still contains unresolved ambiguity"
            )
            return self._outcome(
                NextAction(
                    action="request_input",
                    reason=reason,
                    requested_inputs=requested,
                )
            )

        return self._outcome(
            NextAction(
                action="finish",
                reason="No unused allowed read is expected to add material evidence.",
            )
        )

    def _outcome(self, action: NextAction) -> PlannerOutcome:
        return PlannerOutcome(
            action=action,
            prompt_version=self.version,
            model="offline-deterministic",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


__all__ = [
    "CORE_AGENT_TOOLS",
    "DeterministicEvidencePlanner",
    "EvidencePlanner",
    "ModelEvidencePlanner",
    "PlannerOutcome",
]

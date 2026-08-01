from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import ValidationError

from release_proof.domain.models import (
    AcceptanceCriterion,
    AnalysisRequest,
    ChangeSummary,
    EvidenceItem,
    EvidenceKind,
    NextAction,
)
from release_proof.evidence.ledger import EvidenceLedger
from release_proof.graph.collector import _diff_kind
from release_proof.graph.planner import CORE_AGENT_TOOLS
from release_proof.tools.registry import ReadOnlyToolRegistry, ToolCall, ToolObservation


class ToolHarnessRejection(ValueError):
    """A planner proposal failed a deterministic admission rule."""

    def __init__(self, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.recoverable = recoverable


@dataclass(frozen=True)
class BootstrapResult:
    summary: ChangeSummary
    requirement_text: str
    requirement_evidence: EvidenceItem
    observations: list[dict[str, Any]]
    seen_action_keys: list[str]


@dataclass(frozen=True)
class ToolExecutionResult:
    action: NextAction
    call: ToolCall
    observation: ToolObservation
    evidence: list[EvidenceItem]
    added_evidence: int


def _observation_summary(observation: ToolObservation) -> dict[str, Any]:
    return {
        "name": observation.name,
        "call_key": observation.call_key,
        "status": observation.status,
        "error_category": observation.error_category,
        "duration_ms": observation.duration_ms,
    }


class EvidenceToolHarness:
    """Rebuildable, read-only execution boundary for planner-selected actions."""

    _EXPECTED_KINDS_BY_TOOL: ClassVar[dict[str, set[EvidenceKind]]] = {
        "read_diff": {
            EvidenceKind.DIFF,
            EvidenceKind.MIGRATION,
            EvidenceKind.API_DIFF,
            EvidenceKind.CONFIG,
        },
        "read_file": {EvidenceKind.FILE},
        "search_code": {EvidenceKind.FILE},
        "read_test_report": {EvidenceKind.TEST_RESULT, EvidenceKind.COVERAGE},
        "read_ci_summary": {EvidenceKind.CI},
    }

    _ACTUAL_KINDS_BY_EXPECTATION: ClassVar[dict[EvidenceKind, set[EvidenceKind]]] = {
        EvidenceKind.DIFF: {
            EvidenceKind.DIFF,
            EvidenceKind.MIGRATION,
            EvidenceKind.API_DIFF,
            EvidenceKind.CONFIG,
        },
        EvidenceKind.TEST_RESULT: {
            EvidenceKind.TEST_RESULT,
            EvidenceKind.COVERAGE,
        },
    }

    def bootstrap(self, request: AnalysisRequest) -> BootstrapResult:
        registry = ReadOnlyToolRegistry(
            request.repository_path,
            max_calls=request.limits.max_tool_calls,
        )
        observations: list[dict[str, Any]] = []
        seen: list[str] = []

        summary_call = ToolCall(
            name="get_change_summary",
            arguments={"base_ref": request.base_ref, "head_ref": request.head_ref},
        )
        summary_observation = registry.execute(summary_call)
        observations.append(_observation_summary(summary_observation))
        seen.append(summary_observation.call_key)
        if summary_observation.status != "ok" or summary_observation.output is None:
            raise RuntimeError("required get_change_summary bootstrap failed")
        summary = ChangeSummary.model_validate(summary_observation.output)

        source = request.requirement_source
        if source.kind == "inline":
            requirement_text = source.content or ""
            requirement_uri = source.source_uri or "inline://requirement"
            requirement_locator = "body"
        else:
            if not source.path:
                raise ValueError("requirement path is missing")
            if len(observations) >= request.limits.max_tool_calls:
                raise RuntimeError("tool budget is too small to read the requirement")
            requirement_call = ToolCall(
                name="read_file",
                arguments={
                    "revision": summary.head_ref,
                    "path": source.path,
                    "start_line": 1,
                    "end_line": 500,
                },
            )
            requirement_observation = registry.execute(requirement_call)
            observations.append(_observation_summary(requirement_observation))
            seen.append(requirement_observation.call_key)
            if requirement_observation.status != "ok" or requirement_observation.output is None:
                raise RuntimeError("required read_file bootstrap failed")
            requirement_text = str(requirement_observation.output)
            if source.kind == "github_snapshot":
                try:
                    payload = json.loads(requirement_text)
                    requirement_text = (
                        f"{payload.get('title', '')}\n\n{payload.get('body', '')}".strip()
                    )
                except (json.JSONDecodeError, AttributeError) as exc:
                    raise ValueError("invalid GitHub requirement snapshot") from exc
            requirement_uri = (
                f"{registry.git.root.as_uri()}?revision={summary.head_ref}&path={source.path}"
            )
            requirement_locator = source.path

        requirement_evidence = EvidenceItem.from_observation(
            evidence_id="requirement-1",
            kind=EvidenceKind.REQUIREMENT,
            source_uri=requirement_uri,
            locator=requirement_locator,
            content=requirement_text,
            observed_by="bootstrap_requirement:v1",
        )
        return BootstrapResult(
            summary=summary,
            requirement_text=requirement_text,
            requirement_evidence=requirement_evidence,
            observations=observations,
            seen_action_keys=seen,
        )

    def execute(
        self,
        *,
        request: AnalysisRequest,
        summary: ChangeSummary,
        criteria: list[AcceptanceCriterion],
        action: NextAction,
        existing_evidence: list[EvidenceItem],
    ) -> ToolExecutionResult:
        if action.action != "call_tool" or not action.tool_name:
            raise ToolHarnessRejection("only call_tool actions can enter the tool harness")
        if action.tool_name not in CORE_AGENT_TOOLS:
            raise ToolHarnessRejection(f"tool {action.tool_name!r} is not in the agent allowlist")
        self._validate_expected_kind(action)
        known_criteria = {item.id for item in criteria}
        unknown_targets = sorted(set(action.target_criterion_ids) - known_criteria)
        if unknown_targets:
            raise ToolHarnessRejection(
                "planner targeted unknown acceptance criteria: " + ", ".join(unknown_targets)
            )
        call = self.build_call(action=action, request=request, summary=summary)

        registry = ReadOnlyToolRegistry(request.repository_path, max_calls=1)
        self._validate_context(call, request, summary, registry)
        observation = registry.execute(call)
        evidence = self._to_evidence(
            registry=registry,
            summary=summary,
            action=action,
            call=call,
            observation=observation,
        )
        if observation.status == "ok" and not self._evidence_kinds_match(action, evidence):
            observation = observation.model_copy(
                update={
                    "status": "error",
                    "output": None,
                    "error_category": "EvidenceKindMismatch",
                }
            )
            evidence = []
        ledger = EvidenceLedger(existing_evidence)
        added = ledger.extend(evidence)
        return ToolExecutionResult(
            action=action,
            call=call,
            observation=observation,
            evidence=evidence,
            added_evidence=added,
        )

    @classmethod
    def _validate_expected_kind(cls, action: NextAction) -> None:
        expected = action.expected_evidence_kind
        if expected is None:
            raise ToolHarnessRejection("call_tool must declare expected_evidence_kind")
        allowed = cls._EXPECTED_KINDS_BY_TOOL.get(action.tool_name or "", set())
        if expected not in allowed:
            raise ToolHarnessRejection(
                f"{action.tool_name} cannot produce expected evidence kind {expected.value}"
            )

    @classmethod
    def _evidence_kinds_match(
        cls,
        action: NextAction,
        evidence: list[EvidenceItem],
    ) -> bool:
        expected = action.expected_evidence_kind
        if expected is None:
            return False
        allowed = cls._ACTUAL_KINDS_BY_EXPECTATION.get(expected, {expected})
        return all(item.kind in allowed for item in evidence)

    @staticmethod
    def build_call(
        *,
        action: NextAction,
        request: AnalysisRequest,
        summary: ChangeSummary,
    ) -> ToolCall:
        if not action.tool_name:
            raise ToolHarnessRejection("call_tool needs a tool name")
        arguments = dict(action.arguments)
        if action.tool_name == "read_diff":
            if arguments.get("base_ref") not in {request.base_ref, summary.base_ref}:
                raise ToolHarnessRejection("read_diff base ref does not match the analyzed request")
            if arguments.get("head_ref") not in {request.head_ref, summary.head_ref}:
                raise ToolHarnessRejection("read_diff head ref does not match the analyzed request")
            arguments["base_ref"] = summary.base_ref
            arguments["head_ref"] = summary.head_ref
        elif action.tool_name in {"read_file", "search_code"}:
            revision = str(arguments.get("revision", request.head_ref))
            if revision == request.base_ref:
                arguments["revision"] = summary.base_ref
            elif revision == request.head_ref:
                arguments["revision"] = summary.head_ref
        try:
            return ToolCall(name=action.tool_name, arguments=arguments)  # type: ignore[arg-type]
        except ValidationError as exc:
            raise ToolHarnessRejection(
                "tool arguments did not match the registered schema"
            ) from exc

    @staticmethod
    def _validate_context(
        call: ToolCall,
        request: AnalysisRequest,
        summary: ChangeSummary,
        registry: ReadOnlyToolRegistry,
    ) -> None:
        arguments = call.arguments
        if call.name == "read_diff":
            if (
                arguments.get("base_ref") != summary.base_ref
                or arguments.get("head_ref") != summary.head_ref
            ):
                raise ToolHarnessRejection("read_diff refs must match the bootstrapped revision")
            if arguments.get("path") not in summary.changed_files:
                raise ToolHarnessRejection("read_diff path is not in the changed-file manifest")
        elif call.name in {"read_file", "search_code"}:
            revision = str(arguments.get("revision", summary.head_ref))
            if revision not in {summary.base_ref, summary.head_ref}:
                raise ToolHarnessRejection("source reads are locked to the analyzed revisions")
        elif call.name == "read_test_report":
            EvidenceToolHarness._validate_declared_report(
                str(arguments.get("path", "")),
                request.report_paths,
                registry,
                "test report",
            )
        elif call.name == "read_ci_summary":
            allowed = [request.ci_snapshot_path] if request.ci_snapshot_path else []
            EvidenceToolHarness._validate_declared_report(
                str(arguments.get("path", "")),
                [item for item in allowed if item],
                registry,
                "CI snapshot",
            )

    @staticmethod
    def _validate_declared_report(
        proposed: str,
        declared: list[str],
        registry: ReadOnlyToolRegistry,
        label: str,
    ) -> None:
        if not proposed or not declared:
            raise ToolHarnessRejection(f"{label} was not declared by the user")
        try:
            proposed_path = registry.git.policy.validate_external_report(proposed)
            declared_paths = {
                registry.git.policy.validate_external_report(item) for item in declared
            }
        except (OSError, ValueError) as exc:
            raise ToolHarnessRejection(
                f"{label} failed the repository path policy",
                recoverable=True,
            ) from exc
        if proposed_path not in declared_paths:
            raise ToolHarnessRejection(f"{label} is outside the declared input set")

    @staticmethod
    def _to_evidence(
        *,
        registry: ReadOnlyToolRegistry,
        summary: ChangeSummary,
        action: NextAction,
        call: ToolCall,
        observation: ToolObservation,
    ) -> list[EvidenceItem]:
        if observation.status != "ok" or observation.output is None:
            return []
        metadata = {
            "tool": call.name,
            "call_key": observation.call_key,
            "target_criterion_ids": action.target_criterion_ids,
            "planner_reason": action.reason,
        }
        if call.name in {"read_test_report", "read_ci_summary"}:
            if not isinstance(observation.output, list):
                return []
            return [
                EvidenceItem.model_validate(item).model_copy(
                    update={
                        "metadata": {
                            **EvidenceItem.model_validate(item).metadata,
                            **metadata,
                        }
                    }
                )
                for item in observation.output
            ]

        output = observation.output
        if output == "" or output == [] or output == {}:
            return []
        path = str(call.arguments.get("path", ""))
        if call.name == "read_diff":
            kind = _diff_kind(path)
            locator = path
            source_uri = (
                f"{registry.git.root.as_uri()}?base={summary.base_ref}&head={summary.head_ref}"
            )
            revision = summary.head_ref
            content = str(output)
        elif call.name == "read_file":
            kind = EvidenceKind.FILE
            locator = (
                f"{path}:{call.arguments.get('start_line', 1)}-"
                f"{call.arguments.get('end_line', 240)}"
            )
            revision = str(call.arguments.get("revision", summary.head_ref))
            source_uri = f"{registry.git.root.as_uri()}?revision={revision}&path={path}"
            content = str(output)
        else:
            kind = EvidenceKind.FILE
            locator = f"search:{call.arguments.get('pattern', '')}"
            revision = str(call.arguments.get("revision", summary.head_ref))
            source_uri = f"{registry.git.root.as_uri()}?revision={revision}"
            content = json.dumps(output, ensure_ascii=False)

        stable_suffix = hashlib.sha256(
            f"{observation.call_key}:{kind.value}:{locator}".encode()
        ).hexdigest()[:16]
        return [
            EvidenceItem.from_observation(
                evidence_id=f"agent-{stable_suffix}",
                kind=kind,
                source_uri=source_uri,
                revision=revision,
                locator=locator,
                content=content,
                observed_by=f"planner_tool:{call.name}:v1",
                metadata=metadata,
            )
        ]


__all__ = [
    "BootstrapResult",
    "EvidenceToolHarness",
    "ToolExecutionResult",
    "ToolHarnessRejection",
]

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from release_proof.domain.models import (
    AnalysisRequest,
    ChangeSummary,
    EvidenceItem,
    EvidenceKind,
)
from release_proof.evidence.ledger import EvidenceLedger
from release_proof.graph.budget import ExecutionBudget
from release_proof.tools.registry import ReadOnlyToolRegistry, ToolCall, ToolObservation


@dataclass
class CollectedFacts:
    change_summary: ChangeSummary
    requirement_text: str
    requirement_evidence: EvidenceItem
    evidence: list[EvidenceItem]
    tool_count: int
    tool_observations: list[dict[str, str | int | None]]
    budget: dict[str, int | str]
    warnings: list[str]
    stop_reason: str | None = None


def _diff_kind(path: str) -> EvidenceKind:
    lowered = path.replace("\\", "/").lower()
    if "migration" in lowered or lowered.endswith(".sql") or "alembic/" in lowered:
        return EvidenceKind.MIGRATION
    if any(
        hint in lowered
        for hint in ("docker", ".github/workflows/", "deploy/", "config/", ".env.example")
    ):
        return EvidenceKind.CONFIG
    if "openapi" in lowered or "swagger" in lowered:
        return EvidenceKind.API_DIFF
    return EvidenceKind.DIFF


class EvidenceCollector:
    version = "collector-v2"

    def collect(self, request: AnalysisRequest) -> CollectedFacts:
        warnings: list[str] = []
        budget = ExecutionBudget(request.limits)
        registry = ReadOnlyToolRegistry(
            request.repository_path,
            max_calls=request.limits.max_tool_calls,
        )
        tool_observations: list[dict[str, str | int | None]] = []

        def execute(
            call: ToolCall,
            *,
            required: bool = False,
        ) -> ToolObservation | None:
            action_key = registry.action_key(call)
            if not budget.record_tool(action_key):
                warnings.append(
                    f"stopped before {call.name}: {budget.stop_reason or 'budget exhausted'}"
                )
                return None
            observation = registry.execute(call)
            tool_observations.append(
                {
                    "name": observation.name,
                    "call_key": observation.call_key,
                    "status": observation.status,
                    "error_category": observation.error_category,
                    "duration_ms": observation.duration_ms,
                }
            )
            budget.record_step(added_evidence=1 if observation.status == "ok" else 0)
            if observation.status == "error":
                warnings.append(
                    f"{call.name} returned {observation.error_category or 'tool_error'}"
                )
                if required:
                    raise RuntimeError(f"required read-only tool {call.name} failed")
            return observation

        summary_observation = execute(
            ToolCall(
                name="get_change_summary",
                arguments={"base_ref": request.base_ref, "head_ref": request.head_ref},
            ),
            required=True,
        )
        if summary_observation is None or summary_observation.output is None:
            raise RuntimeError("analysis budget is too small for the change summary")
        summary = ChangeSummary.model_validate(summary_observation.output)
        ledger = EvidenceLedger()
        requirement_text, requirement_uri, requirement_locator = self._read_requirement(
            request,
            summary,
            registry,
            execute,
        )
        requirement_evidence = EvidenceItem.from_observation(
            evidence_id="requirement-1",
            kind=EvidenceKind.REQUIREMENT,
            source_uri=requirement_uri,
            locator=requirement_locator,
            content=requirement_text,
            observed_by="read_requirement:v1",
        )
        ledger.add(requirement_evidence)
        for index, path in enumerate(summary.changed_files):
            if len(ledger) >= request.limits.max_evidence_items:
                warnings.append("evidence item limit reached; remaining diffs were not collected")
                break
            observation = execute(
                ToolCall(
                    name="read_diff",
                    arguments={
                        "base_ref": summary.base_ref,
                        "head_ref": summary.head_ref,
                        "path": path,
                    },
                )
            )
            if observation is None:
                break
            if observation.status == "error" or observation.output is None:
                continue
            diff = str(observation.output)
            ledger.add(
                EvidenceItem.from_observation(
                    evidence_id=f"diff-{index + 1}",
                    kind=_diff_kind(path),
                    source_uri=(
                        f"{registry.git.root.as_uri()}?"
                        f"base={summary.base_ref}&head={summary.head_ref}"
                    ),
                    revision=summary.head_ref,
                    locator=path,
                    content=diff,
                    observed_by="read_diff:v1",
                    metadata={"path": path},
                )
            )
        for index, report_path in enumerate(request.report_paths):
            observation = execute(
                ToolCall(
                    name="read_test_report",
                    arguments={
                        "path": report_path,
                        "evidence_prefix": f"report-{index + 1}",
                    },
                )
            )
            if observation is None:
                break
            if observation.status == "ok" and isinstance(observation.output, list):
                ledger.extend(
                    EvidenceItem.model_validate(item) for item in observation.output
                )
        if request.ci_snapshot_path:
            observation = execute(
                ToolCall(
                    name="read_ci_summary",
                    arguments={
                        "path": request.ci_snapshot_path,
                        "evidence_prefix": "ci-snapshot",
                    },
                )
            )
            if (
                observation is not None
                and observation.status == "ok"
                and isinstance(observation.output, list)
            ):
                ledger.extend(
                    EvidenceItem.model_validate(item) for item in observation.output
                )
        return CollectedFacts(
            change_summary=summary,
            requirement_text=requirement_text,
            requirement_evidence=requirement_evidence,
            evidence=ledger.items()[: request.limits.max_evidence_items],
            tool_count=budget.tool_calls,
            tool_observations=tool_observations,
            budget=budget.snapshot(),
            warnings=warnings,
            stop_reason=budget.stop_reason,
        )

    def _read_requirement(
        self,
        request: AnalysisRequest,
        summary: ChangeSummary,
        registry: ReadOnlyToolRegistry,
        execute: Callable[..., ToolObservation | None],
    ) -> tuple[str, str, str]:
        source = request.requirement_source
        if source.kind == "inline":
            return source.content or "", source.source_uri or "inline://requirement", "body"
        if not source.path:
            raise ValueError("requirement path is missing")
        observation = execute(
            ToolCall(
                name="read_file",
                arguments={
                    "revision": summary.head_ref,
                    "path": source.path,
                    "start_line": 1,
                    "end_line": 500,
                },
            ),
            required=True,
        )
        if observation is None or observation.output is None:
            raise RuntimeError("analysis budget is too small for the requirement")
        text = str(observation.output)
        if source.kind == "github_snapshot":
            try:
                payload = json.loads(text)
                title = str(payload.get("title", ""))
                body = str(payload.get("body", ""))
                text = f"{title}\n\n{body}".strip()
            except (json.JSONDecodeError, AttributeError) as exc:
                raise ValueError("invalid GitHub requirement snapshot") from exc
        source_uri = (
            f"{registry.git.root.as_uri()}?revision={summary.head_ref}&path={source.path}"
        )
        return text, source_uri, source.path

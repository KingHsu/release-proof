from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from release_proof.adapters.local_git import GitReadOnlyClient
from release_proof.adapters.reports import ReportCollector
from release_proof.domain.models import (
    AnalysisRequest,
    ChangeSummary,
    EvidenceItem,
    EvidenceKind,
)
from release_proof.evidence.ledger import EvidenceLedger
from release_proof.tools.policy import ToolPolicy


@dataclass
class CollectedFacts:
    change_summary: ChangeSummary
    requirement_text: str
    requirement_evidence: EvidenceItem
    evidence: list[EvidenceItem]
    tool_count: int
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
    version = "collector-v1"

    def collect(self, request: AnalysisRequest) -> CollectedFacts:
        started_at = time.monotonic()
        git = GitReadOnlyClient.for_repository(request.repository_path)
        policy = git.policy
        tool_count = 0

        def consume_tool(name: str) -> bool:
            nonlocal tool_count
            if tool_count >= request.limits.max_tool_calls:
                warnings.append(f"stopped before {name}: tool call limit reached")
                return False
            if time.monotonic() - started_at >= request.limits.max_elapsed_seconds:
                warnings.append(f"stopped before {name}: elapsed time limit reached")
                return False
            tool_count += 1
            return True

        warnings: list[str] = []
        if not consume_tool("get_change_summary"):
            raise RuntimeError("analysis budget is too small for the change summary")
        summary = git.get_change_summary(request.base_ref, request.head_ref)
        ledger = EvidenceLedger()
        if not consume_tool("read_requirement"):
            raise RuntimeError("analysis budget is too small for the requirement")
        requirement_text, requirement_uri, requirement_locator = self._read_requirement(request, policy)
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
            if not consume_tool("read_diff"):
                break
            try:
                diff = git.read_diff(summary.base_ref, summary.head_ref, path)
            except Exception as exc:
                warnings.append(f"could not read bounded diff for {path}: {type(exc).__name__}")
                continue
            ledger.add(
                EvidenceItem.from_observation(
                    evidence_id=f"diff-{index + 1}",
                    kind=_diff_kind(path),
                    source_uri=f"{git.root.as_uri()}?base={summary.base_ref}&head={summary.head_ref}",
                    revision=summary.head_ref,
                    locator=path,
                    content=diff,
                    observed_by="read_diff:v1",
                    metadata={"path": path},
                )
            )
        reports = ReportCollector(policy)
        for index, report_path in enumerate(request.report_paths):
            if not consume_tool("read_test_report"):
                break
            try:
                ledger.extend(reports.read(report_path, evidence_prefix=f"report-{index + 1}"))
            except Exception as exc:
                warnings.append(f"report {Path(report_path).name} was not accepted: {type(exc).__name__}")
        if request.ci_snapshot_path and consume_tool("read_ci_summary"):
            try:
                ledger.extend(
                    reports.read_ci_snapshot(
                        request.ci_snapshot_path, evidence_prefix="ci-snapshot"
                    )
                )
            except Exception as exc:
                warnings.append(f"CI snapshot was not accepted: {type(exc).__name__}")
        stop_reason = None
        if tool_count >= request.limits.max_tool_calls:
            stop_reason = "tool_call_limit"
        elif time.monotonic() - started_at >= request.limits.max_elapsed_seconds:
            stop_reason = "elapsed_time_limit"
        return CollectedFacts(
            change_summary=summary,
            requirement_text=requirement_text,
            requirement_evidence=requirement_evidence,
            evidence=ledger.items()[: request.limits.max_evidence_items],
            tool_count=tool_count,
            warnings=warnings,
            stop_reason=stop_reason,
        )

    def _read_requirement(
        self, request: AnalysisRequest, policy: ToolPolicy
    ) -> tuple[str, str, str]:
        source = request.requirement_source
        if source.kind == "inline":
            return source.content or "", source.source_uri or "inline://requirement", "body"
        if not source.path:
            raise ValueError("requirement path is missing")
        path = policy.validate_readable_file(source.path)
        text = path.read_text(encoding="utf-8")
        if source.kind == "github_snapshot":
            try:
                payload = json.loads(text)
                title = str(payload.get("title", ""))
                body = str(payload.get("body", ""))
                text = f"{title}\n\n{body}".strip()
            except (json.JSONDecodeError, AttributeError) as exc:
                raise ValueError("invalid GitHub requirement snapshot") from exc
        return text, path.as_uri(), "body"

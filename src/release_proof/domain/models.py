from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class CriterionType(str, Enum):
    FUNCTIONAL = "functional"
    ERROR_HANDLING = "error_handling"
    COMPATIBILITY = "compatibility"
    DATA = "data"
    OBSERVABILITY = "observability"
    DEPLOYMENT = "deployment"
    DOCUMENTATION = "documentation"


class CriterionStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"
    UNABLE_TO_DETERMINE = "unable_to_determine"


class Recommendation(str, Enum):
    READY_FOR_HUMAN_REVIEW = "ready_for_human_review"
    CONDITIONAL = "conditional"
    NOT_READY = "not_ready"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ANALYSIS_FAILED = "analysis_failed"


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


class EvidenceKind(str, Enum):
    REQUIREMENT = "requirement"
    DIFF = "diff"
    FILE = "file"
    TEST_RESULT = "test_result"
    COVERAGE = "coverage"
    CI = "ci"
    API_DIFF = "api_diff"
    MIGRATION = "migration"
    CONFIG = "config"
    HUMAN_INPUT = "human_input"


class RiskDomain(str, Enum):
    API_CONTRACT = "api_contract"
    DATA_MIGRATION = "data_migration"
    BUSINESS_LOGIC = "business_logic"
    TESTS = "tests"
    CONFIG_DEPLOYMENT = "config_deployment"
    SCHEDULED_ASYNC = "scheduled_async"
    DOCS_ONLY = "docs_only"


class RequirementSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["inline", "file", "github_snapshot"] = "inline"
    content: str | None = None
    path: str | None = None
    source_uri: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> RequirementSource:
        if self.kind == "inline" and not (self.content and self.content.strip()):
            raise ValueError("inline requirement source needs non-empty content")
        if self.kind in {"file", "github_snapshot"} and not self.path:
            raise ValueError(f"{self.kind} requirement source needs path")
        return self


class AnalysisLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=18, ge=1, le=100)
    max_tool_calls: int = Field(default=30, ge=3, le=200)
    max_no_progress: int = Field(default=2, ge=1, le=10)
    max_elapsed_seconds: int = Field(default=120, ge=5, le=1800)
    max_evidence_items: int = Field(default=250, ge=10, le=2000)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_path: str
    base_ref: str = "HEAD~1"
    head_ref: str = "HEAD"
    requirement_source: RequirementSource
    report_paths: list[str] = Field(default_factory=list)
    ci_snapshot_path: str | None = None
    mode: Literal["auto", "single", "multi"] = "auto"
    require_verification_evidence: bool = True
    continue_without_reports: bool = False
    limits: AnalysisLimits = Field(default_factory=AnalysisLimits)

    @model_validator(mode="after")
    def validate_refs(self) -> AnalysisRequest:
        for label, ref in (("base_ref", self.base_ref), ("head_ref", self.head_ref)):
            if not ref or ref.startswith("-") or "\x00" in ref:
                raise ValueError(f"unsafe {label}")
        return self


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    kind: EvidenceKind
    source_uri: str
    locator: str
    content_hash: str


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: EvidenceKind
    source_uri: str
    revision: str = ""
    locator: str
    content_excerpt: str = Field(max_length=4000)
    content_hash: str
    observed_by: str
    observed_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_observation(
        cls,
        *,
        evidence_id: str,
        kind: EvidenceKind,
        source_uri: str,
        locator: str,
        content: str,
        observed_by: str,
        revision: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        normalized = content.replace("\r\n", "\n")
        return cls(
            id=evidence_id,
            kind=kind,
            source_uri=source_uri,
            revision=revision,
            locator=locator,
            content_excerpt=normalized[:4000],
            content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            observed_by=observed_by,
            metadata=metadata or {},
        )

    def as_ref(self) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=self.id,
            kind=self.kind,
            source_uri=self.source_uri,
            locator=self.locator,
            content_hash=self.content_hash,
        )


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    statement: str = Field(min_length=3, max_length=1200)
    type: CriterionType = CriterionType.FUNCTIONAL
    verification_hint: str | None = None
    source_ref: EvidenceRef
    ambiguity: list[str] = Field(default_factory=list)
    critical: bool = False


class ChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_ref: str
    head_ref: str
    changed_files: list[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0


class ChangeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed_files: int
    changed_lines: int
    risk_domains: set[RiskDomain] = Field(default_factory=set)
    requires_human_input: bool = False
    recommended_mode: Literal["single", "multi"] = "single"
    reasons: list[str] = Field(default_factory=list)


class RiskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    domain: RiskDomain
    severity: Literal["low", "medium", "high", "critical"]
    statement: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    needs_human_check: bool = False


class DomainAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: RiskDomain
    summary: str
    risks: list[RiskItem] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    specialist: str
    status: Literal["complete", "partial", "failed"] = "complete"
    prompt_version: str | None = None
    model: str | None = None
    usage: dict[str, int | float | str] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)


class AcceptanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    criterion: str
    critical: bool = False
    status: CriterionStatus
    implementation_evidence: list[EvidenceRef] = Field(default_factory=list)
    verification_evidence: list[EvidenceRef] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    explanation: str


class HumanCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    reason: str
    blocking: bool = False


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int
    node: str
    status: Literal["started", "completed", "paused", "failed", "skipped"]
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    tool: str | None = None
    duration_ms: int | None = None
    version: str = "v1"
    prompt_version: str | None = None
    model: str | None = None
    usage: dict[str, int | float | str] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ReleaseAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    change_summary: ChangeSummary
    change_profile: ChangeProfile
    acceptance_matrix: list[AcceptanceResult]
    domain_risks: list[RiskItem] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    human_checks: list[HumanCheck] = Field(default_factory=list)
    rollback_notes: list[str] = Field(default_factory=list)
    recommendation: Recommendation
    evidence_index: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    active_skills: list[str] = Field(default_factory=list)
    route: Literal["single", "multi"] = "single"
    stop_reason: str = "completed"
    prompt_versions: list[str] = Field(default_factory=list)
    llm_usage: dict[str, int | float] = Field(default_factory=dict)


class InterruptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    reasons: list[str]
    requested_inputs: list[str]


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_paths: list[str] = Field(default_factory=list)
    ci_snapshot_path: str | None = None
    clarifications: dict[str, str] = Field(default_factory=dict)
    continue_without_reports: bool = False


class AnalysisRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    thread_id: str
    status: RunStatus
    request: AnalysisRequest
    report: ReleaseAssessment | None = None
    interrupt: InterruptPayload | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


def path_uri(path: str | Path) -> str:
    return Path(path).resolve().as_uri()

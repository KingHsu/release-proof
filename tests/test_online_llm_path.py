from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel

from release_proof.adapters.llm import DeepSeekAnthropicClient, FakeStructuredLLM
from release_proof.domain.models import (
    AcceptanceCriterion,
    AnalysisRequest,
    EvidenceItem,
    EvidenceKind,
    RequirementSource,
    RiskDomain,
)
from release_proof.graph.specialists import SpecialistCoordinator
from release_proof.graph.workflow import WorkflowNodes
from release_proof.requirements.extractor import LLMAcceptanceExtractor
from tests.helpers import make_git_repo


def fake_llm() -> FakeStructuredLLM:
    return FakeStructuredLLM(
        responses={
            "ExtractedCriteriaEnvelope": {
                "criteria": [
                    {
                        "statement": "Health API returns an ok status",
                        "type": "functional",
                        "verification_hint": "JUnit test",
                        "ambiguity": [],
                        "critical": False,
                    }
                ]
            },
            "DomainAssessmentDraft": {
                "summary": "A bounded model assessment that still requires policy review.",
                "risks": [
                    {
                        "severity": "high",
                        "statement": "Compatibility needs human confirmation.",
                        "evidence_ids": ["not-in-context"],
                        "needs_human_check": True,
                    }
                ],
                "missing_evidence": [],
            },
        }
    )


def requirement_evidence() -> EvidenceItem:
    return EvidenceItem.from_observation(
        evidence_id="requirement-1",
        kind=EvidenceKind.REQUIREMENT,
        source_uri="inline://requirement",
        locator="body",
        content="- Health API returns an ok status",
        observed_by="test",
    )


def test_llm_extractor_uses_versioned_prompt_and_schema() -> None:
    llm = fake_llm()
    outcome = LLMAcceptanceExtractor(llm).extract_outcome(
        "- Health API returns an ok status", requirement_evidence()
    )
    assert outcome.criteria[0].statement == "Health API returns an ok status"
    assert outcome.prompt_version == "extract-acceptance-criteria-v1"
    assert outcome.usage["input_tokens"] > 0
    assert llm.calls[0]["schema"] == "ExtractedCriteriaEnvelope"
    assert "untrusted data" in llm.calls[0]["system"]


def test_deepseek_client_forces_schema_bound_tool_output() -> None:
    class SampleOutput(BaseModel):
        answer: str

    class RecordingMessages:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_structured_response",
                        input={"answer": "bounded"},
                    )
                ],
                usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            )

    messages = RecordingMessages()
    client = DeepSeekAnthropicClient.__new__(DeepSeekAnthropicClient)
    object.__setattr__(client, "_client", SimpleNamespace(messages=messages))
    client.model = "deepseek-test"

    parsed, usage = client.structured(
        system="system",
        user="user",
        schema=SampleOutput,
    )

    assert parsed == SampleOutput(answer="bounded")
    assert usage["input_tokens"] == 11
    assert messages.kwargs["tools"][0]["input_schema"] == SampleOutput.model_json_schema()
    assert messages.kwargs["tool_choice"] == {
        "type": "tool",
        "name": "submit_structured_response",
    }


def test_workflow_records_online_prompt_and_usage(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    llm = fake_llm()
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills", llm=llm)
    request = AnalysisRequest(
        repository_path=str(repo),
        base_ref="HEAD~1",
        head_ref="HEAD",
        requirement_source=RequirementSource(
            kind="inline", content="- Health API returns an ok status"
        ),
        continue_without_reports=True,
    )
    state, interrupt = nodes.run_manual(
        {
            "run_id": "online-fake-run",
            "request": request.model_dump(mode="json"),
            "trace": [],
            "warnings": [],
            "errors": [],
            "tool_count": 0,
            "step_count": 0,
            "budget_exhausted": False,
            "stop_reason": "completed",
            "prompt_versions": [],
            "llm_usage": {"input_tokens": 0, "output_tokens": 0},
            "llm_call_count": 0,
        }
    )
    assert interrupt is None
    assert "extract-acceptance-criteria-v1" in state["prompt_versions"]
    assert state["llm_usage"]["input_tokens"] > 0
    extract_trace = next(item for item in state["trace"] if item["node"] == "extract_acceptance_criteria")
    assert extract_trace["model"] == "fake-structured-llm"
    assert sum(call["schema"] == "DomainAssessmentDraft" for call in llm.calls) == 1
    route_trace = next(item for item in state["trace"] if item["node"] == "route_analysis")
    assert route_trace["model"] == "fake-structured-llm"
    assert route_trace["skills"] == [
        "release-readiness-review",
        "api-compatibility-review",
    ]


def test_shared_llm_call_budget_falls_back_before_specialist(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo-budget")
    llm = fake_llm()
    nodes = WorkflowNodes(
        Path(__file__).parents[1] / "skills",
        llm=llm,
        max_llm_calls=1,
        max_output_tokens=256,
    )
    request = AnalysisRequest(
        repository_path=str(repo),
        base_ref="HEAD~1",
        head_ref="HEAD",
        requirement_source=RequirementSource(
            kind="inline", content="- Health API returns an ok status"
        ),
        continue_without_reports=True,
    )
    state, interrupt = nodes.run_manual(
        {
            "run_id": "online-budget-run",
            "request": request.model_dump(mode="json"),
            "trace": [],
            "warnings": [],
            "errors": [],
            "tool_count": 0,
            "step_count": 0,
            "budget_exhausted": False,
            "stop_reason": "completed",
            "prompt_versions": [],
            "llm_usage": {"input_tokens": 0, "output_tokens": 0},
            "llm_call_count": 0,
        }
    )

    assert interrupt is None
    assert len(llm.calls) == 1
    assert llm.calls[0]["schema"] == "ExtractedCriteriaEnvelope"
    assert llm.calls[0]["max_tokens"] == 256
    assert state["llm_usage"]["calls"] == 1
    assert any("deterministic specialist fallback" in item for item in state["warnings"])
    route_trace = next(item for item in state["trace"] if item["node"] == "route_analysis")
    assert route_trace["model"] is None
    assert "scheduled 0 bounded LLM call(s)" in route_trace["summary"]


def test_multi_specialists_call_llm_but_drop_unknown_evidence_ids() -> None:
    llm = fake_llm()
    criterion = AcceptanceCriterion(
        id="AC-001",
        statement="API and migration remain compatible",
        source_ref=requirement_evidence().as_ref(),
        critical=True,
    )
    evidence = [
        EvidenceItem.from_observation(
            evidence_id="api-1",
            kind=EvidenceKind.API_DIFF,
            source_uri="git://repo",
            locator="openapi.json",
            content="API path changed",
            observed_by="test",
        ),
        EvidenceItem.from_observation(
            evidence_id="migration-1",
            kind=EvidenceKind.MIGRATION,
            source_uri="git://repo",
            locator="migrations/002.sql",
            content="migration changed",
            observed_by="test",
        ),
    ]
    reports = SpecialistCoordinator(llm=llm).run(
        {RiskDomain.API_CONTRACT, RiskDomain.DATA_MIGRATION},
        [criterion],
        evidence,
        route="multi",
        skill_context=[
            {
                "name": "release-readiness-review",
                "version": "1.0.0",
                "instructions": "COMMON-SKILL-SENTINEL",
            },
            {
                "name": "api-compatibility-review",
                "version": "1.0.0",
                "instructions": "API-SKILL-SENTINEL",
            },
            {
                "name": "database-migration-review",
                "version": "1.0.0",
                "instructions": "MIGRATION-SKILL-SENTINEL",
            },
        ],
    )
    assert len(reports) == 2
    assert all(report.prompt_version for report in reports)
    assert all(report.model == "fake-structured-llm" for report in reports)
    assert all(report.risks[0].evidence == [] for report in reports)
    assert all("not in its bounded context" in report.missing_evidence[-1] for report in reports)
    assert sum(call["schema"] == "DomainAssessmentDraft" for call in llm.calls) == 2
    api_call = next(call for call in llm.calls if "api-1" in call["user"])
    migration_call = next(call for call in llm.calls if "migration-1" in call["user"])
    assert "API-SKILL-SENTINEL" in api_call["user"]
    assert "MIGRATION-SKILL-SENTINEL" not in api_call["user"]
    assert "MIGRATION-SKILL-SENTINEL" in migration_call["user"]
    assert "API-SKILL-SENTINEL" not in migration_call["user"]

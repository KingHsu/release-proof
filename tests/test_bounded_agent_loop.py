from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from release_proof.adapters.llm import FakeStructuredLLM, StructuredOutputError
from release_proof.domain.models import (
    AnalysisRequest,
    Recommendation,
    RequirementSource,
    ResumeRequest,
)
from release_proof.graph.workflow import WorkflowNodes, WorkflowState
from tests.helpers import make_git_repo, write_junit, write_passing_junit


def initial_state(request: AnalysisRequest, run_id: str = "bounded-agent-test") -> WorkflowState:
    return {
        "run_id": run_id,
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
        "agent_steps_used": 0,
        "no_progress_count": 0,
        "seen_action_keys": [],
        "action_history": [],
    }


def fake_with_actions(actions: list[dict[str, Any]]) -> FakeStructuredLLM:
    return FakeStructuredLLM(
        responses=cast(
            dict[str, Any],
            {
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
                "NextAction": actions,
                "DomainAssessmentDraft": {
                    "summary": "The API evidence is bounded and still subject to policy.",
                    "risks": [],
                    "evidence_ids": [],
                    "missing_evidence": [],
                },
            },
        )
    )


def request_for(repo: Path, **updates) -> AnalysisRequest:
    payload = {
        "repository_path": str(repo),
        "base_ref": "HEAD~1",
        "head_ref": "HEAD",
        "requirement_source": RequirementSource(
            kind="inline",
            content="- Health API returns an ok status",
        ),
        "continue_without_reports": True,
    }
    payload.update(updates)
    return AnalysisRequest(**payload)


def diff_action() -> dict:
    return {
        "action": "call_tool",
        "tool_name": "read_diff",
        "arguments": {
            "base_ref": "HEAD~1",
            "head_ref": "HEAD",
            "path": "src/api/health.py",
        },
        "target_criterion_ids": ["AC-001"],
        "expected_evidence_kind": "diff",
        "reason": "Inspect the implementation hunk for the acceptance criterion.",
    }


def report_action() -> dict:
    return {
        "action": "call_tool",
        "tool_name": "read_test_report",
        "arguments": {
            "path": "reports/junit.xml",
            "evidence_prefix": "agent-junit",
        },
        "target_criterion_ids": ["AC-001"],
        "expected_evidence_kind": "test_result",
        "reason": "Read the declared JUnit report to obtain verification evidence.",
    }


def finish_action() -> dict:
    return {
        "action": "finish",
        "reason": "The useful bounded reads have been consumed.",
    }


def test_fake_model_drives_diff_report_finish_and_skill_context(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    write_junit(repo)
    request = request_for(repo, report_paths=["reports/junit.xml"])
    llm = fake_with_actions([diff_action(), report_action(), finish_action()])
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills", llm=llm)

    state, interrupt = nodes.run_manual(initial_state(request))

    assert interrupt is None
    assert [call["schema"] for call in llm.calls].count("NextAction") == 3
    planner_calls = [call for call in llm.calls if call["schema"] == "NextAction"]
    assert "release-readiness-review" in planner_calls[0]["user"]
    assert "candidate_read_actions" in planner_calls[0]["user"]
    assert "submit_structured_response" in planner_calls[0]["system"]
    selected_tools = [
        item["tool"]
        for item in state["trace"]
        if item["node"] == "validate_and_execute_readonly_tool"
    ]
    assert selected_tools == ["read_diff", "read_test_report"]
    assert all(
        item["usage"]["planner_selected"] == "true"
        for item in state["trace"]
        if item["node"] == "validate_and_execute_readonly_tool"
    )
    evidence = cast(list[dict[str, Any]], state.get("evidence"))
    assert evidence is not None
    assert any(item["metadata"].get("tool") == "read_diff" for item in evidence)
    assert any(item["metadata"].get("tool") == "read_test_report" for item in evidence)
    report = cast(dict[str, Any], state.get("report"))
    assert report["recommendation"] != Recommendation.ANALYSIS_FAILED.value


def test_live_badcase_shape_maps_passing_junit_to_behavior_criterion(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo-live-badcase")
    write_passing_junit(repo)
    requirement = (
        "The health API must return JSON status=ok, and the repository-local JUnit report "
        "reports/junit.xml must show that the health test passed with zero failures."
    )
    request = request_for(
        repo,
        requirement_source=RequirementSource(kind="inline", content=requirement),
        report_paths=["reports/junit.xml"],
        mode="single",
        continue_without_reports=False,
    )
    llm = FakeStructuredLLM(
        responses=cast(
            dict[str, Any],
            {
                "ExtractedCriteriaEnvelope": {
                    "criteria": [
                        {
                            "statement": "The health API must return JSON with status=ok.",
                            "type": "functional",
                            "ambiguity": [],
                            "critical": True,
                        },
                        {
                            "statement": (
                                "The repository-local JUnit report reports/junit.xml must show "
                                "that the health test passed with zero failures."
                            ),
                            "type": "functional",
                            "ambiguity": [],
                            "critical": True,
                        },
                    ]
                },
                "NextAction": [diff_action(), report_action(), finish_action()],
            },
        )
    )
    nodes = WorkflowNodes(
        Path(__file__).parents[1] / "skills",
        llm=llm,
        max_llm_calls=4,
        max_output_tokens=1200,
    )

    state, interrupt = nodes.run_manual(initial_state(request, "live-badcase-replay"))

    assert interrupt is None
    report = cast(dict[str, Any], state.get("report"))
    result = report["acceptance_matrix"][0]
    assert result["status"] == "supported"
    assert result["implementation_evidence"]
    assert result["verification_evidence"]
    assert any(
        "verification_hint_match" in detail["signals"]
        for detail in result["match_details"]
        if detail["layer"] == "verification"
    )
    assert report["recommendation"] == Recommendation.CONDITIONAL.value
    assert report["human_checks"]


def test_structured_failure_trips_run_circuit_breaker_and_keeps_bounded_evidence_flow(
    tmp_path: Path,
) -> None:
    class FailingStructuredLLM:
        model = "provider-test"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def structured(
            self,
            *,
            system: str,
            user: str,
            schema: type[BaseModel],
            max_tokens: int = 1800,
        ) -> tuple[BaseModel, dict[str, Any]]:
            del system, user, max_tokens
            self.calls.append(schema.__name__)
            raise StructuredOutputError(
                "structured contract rejected",
                usage={"input_tokens": 23, "output_tokens": 7},
            )

    repo = make_git_repo(tmp_path / "repo-degraded")
    write_junit(repo)
    request = request_for(repo, report_paths=["reports/junit.xml"])
    llm = FailingStructuredLLM()
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills", llm=llm)

    state, interrupt = nodes.run_manual(initial_state(request, "degraded-run"))

    assert interrupt is None
    assert llm.calls == ["ExtractedCriteriaEnvelope"]
    assert state.get("llm_degraded") is True
    assert state["llm_usage"] == {
        "input_tokens": 23,
        "output_tokens": 7,
        "calls": 1,
    }
    selected_tools = [
        item["tool"]
        for item in state["trace"]
        if item["node"] == "validate_and_execute_readonly_tool"
    ]
    assert selected_tools == ["read_diff", "read_test_report"]
    report = cast(dict[str, Any], state.get("report"))
    assert report is not None
    assert report["stop_reason"] == "completed"
    assert any("offline baseline used" in limitation for limitation in report["limitations"])


def test_unknown_tool_is_rejected_without_execution(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    llm = fake_with_actions(
        [
            {
                "action": "call_tool",
                "tool_name": "run_shell",
                "arguments": {"command": "git push"},
                "target_criterion_ids": ["AC-001"],
                "reason": "Attempt an operation outside the read-only contract.",
            }
        ]
    )
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills", llm=llm)

    state, interrupt = nodes.run_manual(initial_state(request_for(repo)))

    assert interrupt is None
    assert state["stop_reason"] == "tool_policy_rejected"
    assert state["tool_count"] == 1  # deterministic change-summary bootstrap only
    rejected = next(
        item for item in state["trace"] if item["node"] == "validate_and_execute_readonly_tool"
    )
    assert rejected["status"] == "failed"
    assert rejected["tool"] == "run_shell"
    history = cast(list[dict[str, Any]], state.get("action_history"))
    assert history[0]["status"] == "rejected"


def test_duplicate_action_key_stops_before_second_side_effect(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    llm = fake_with_actions([diff_action(), diff_action()])
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills", llm=llm)

    state, interrupt = nodes.run_manual(initial_state(request_for(repo)))

    assert interrupt is None
    assert state["stop_reason"] == "duplicate_tool_action"
    assert state["tool_count"] == 2  # bootstrap + one admitted diff
    traces = [
        item for item in state["trace"] if item["node"] == "validate_and_execute_readonly_tool"
    ]
    assert [item["status"] for item in traces] == ["completed", "failed"]
    evidence = cast(list[dict[str, Any]], state.get("evidence"))
    assert len([item for item in evidence if item["kind"] == "diff"]) == 1


def test_interrupt_resume_preserves_evidence_keys_and_budget(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    write_junit(repo)
    llm = fake_with_actions(
        [
            diff_action(),
            {
                "action": "request_input",
                "reason": "Verification evidence is not yet declared.",
                "requested_inputs": ["a repository-local JUnit report"],
            },
            report_action(),
            finish_action(),
        ]
    )
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills", llm=llm)
    request = request_for(
        repo,
        continue_without_reports=False,
        require_verification_evidence=True,
    )

    paused, interrupt = nodes.run_manual(initial_state(request, "resume-test"))

    assert interrupt is not None
    prior_evidence = cast(list[dict[str, Any]], paused.get("evidence"))
    prior_ids = {item["id"] for item in prior_evidence}
    prior_keys = set(paused.get("seen_action_keys", []))
    prior_steps = int(paused.get("agent_steps_used", 0))
    prior_tools = paused["tool_count"]
    simulated_now = datetime.now(UTC)
    paused["paused_at"] = (simulated_now - timedelta(minutes=5)).isoformat()
    paused["deadline_at"] = (simulated_now - timedelta(minutes=3)).isoformat()

    resumed, second_interrupt = nodes.resume_manual(
        paused,
        ResumeRequest(report_paths=["reports/junit.xml"]),
    )

    assert second_interrupt is None
    resumed_evidence = cast(list[dict[str, Any]], resumed.get("evidence"))
    assert prior_ids <= {item["id"] for item in resumed_evidence}
    assert prior_keys < set(resumed.get("seen_action_keys", []))
    assert int(resumed.get("agent_steps_used", 0)) > prior_steps
    assert resumed["tool_count"] == prior_tools + 1
    resumed_deadline = resumed.get("deadline_at")
    assert resumed_deadline is not None
    assert datetime.fromisoformat(resumed_deadline) > simulated_now
    assert float(resumed.get("paused_seconds_total", 0)) >= 299
    resumed_report = cast(dict[str, Any], resumed.get("report"))
    assert resumed_report["recommendation"] in {
        Recommendation.CONDITIONAL.value,
        Recommendation.READY_FOR_HUMAN_REVIEW.value,
    }


def test_expected_evidence_kind_mismatch_is_rejected_before_tool_execution(
    tmp_path: Path,
) -> None:
    repo = make_git_repo(tmp_path / "repo")
    mismatched = diff_action()
    mismatched["expected_evidence_kind"] = "ci"
    llm = fake_with_actions([mismatched])
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills", llm=llm)

    state, interrupt = nodes.run_manual(initial_state(request_for(repo)))

    assert interrupt is None
    assert state["stop_reason"] == "tool_policy_rejected"
    assert state["tool_count"] == 1
    rejected = next(
        item for item in state["trace"] if item["node"] == "validate_and_execute_readonly_tool"
    )
    assert rejected["status"] == "failed"
    assert rejected["tool"] == "read_diff"


def test_deadline_expiring_after_planning_is_classified_as_elapsed_time(
    tmp_path: Path,
) -> None:
    repo = make_git_repo(tmp_path / "repo")
    llm = fake_with_actions([diff_action()])
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills", llm=llm)
    state = initial_state(request_for(repo))
    for node in (
        nodes.validate_request,
        nodes.bootstrap_change_facts,
        nodes.extract_acceptance_criteria,
        nodes.profile_change,
        nodes.load_relevant_skills,
        nodes.compute_evidence_gaps,
        nodes.choose_next_action,
    ):
        state = node(state)
    state["deadline_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

    state = nodes.validate_and_execute_tool(state)

    assert state["stop_reason"] == "elapsed_time_limit"
    assert state["budget_exhausted"]
    assert state["tool_count"] == 1
    timeout_trace = state["trace"][-1]
    assert timeout_trace["node"] == "validate_and_execute_readonly_tool"
    assert timeout_trace["status"] == "failed"


def test_recoverable_report_rejection_can_retry_after_resume_and_paths_deduplicate(
    tmp_path: Path,
) -> None:
    repo = make_git_repo(tmp_path / "repo")
    nodes = WorkflowNodes(Path(__file__).parents[1] / "skills")
    request = request_for(
        repo,
        report_paths=["reports/junit.xml"],
        continue_without_reports=True,
    )

    paused, interrupt = nodes.run_manual(initial_state(request, "retry-report"))

    assert interrupt is not None
    report_history = [
        item
        for item in paused.get("action_history", [])
        if item.get("tool_name") == "read_test_report"
    ]
    assert [item["status"] for item in report_history] == ["rejected"]

    report_path = write_junit(repo)
    resumed, second_interrupt = nodes.resume_manual(
        paused,
        ResumeRequest(report_paths=[str(report_path)]),
    )

    assert second_interrupt is None
    resumed_request = AnalysisRequest.model_validate(resumed["request"])
    assert resumed_request.report_paths == ["reports/junit.xml"]
    report_history = [
        item
        for item in resumed.get("action_history", [])
        if item.get("tool_name") == "read_test_report"
    ]
    assert [item["status"] for item in report_history] == ["rejected", "executed"]
    report_traces = [
        item
        for item in resumed["trace"]
        if item["node"] == "validate_and_execute_readonly_tool"
        and item["tool"] == "read_test_report"
    ]
    assert [item["status"] for item in report_traces] == ["failed", "completed"]

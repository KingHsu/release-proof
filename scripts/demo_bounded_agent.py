from __future__ import annotations

import argparse
import json
import re
import subprocess
import uuid
from pathlib import Path

from pydantic import BaseModel

from release_proof.adapters.llm import FakeStructuredLLM
from release_proof.domain.models import (
    AnalysisRequest,
    ReleaseAssessment,
    RequirementSource,
)
from release_proof.graph.workflow import WorkflowNodes, WorkflowState
from release_proof.reporting import ReportWriter


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "git demo command failed")
    return completed.stdout


def create_demo_repository(root: Path) -> Path:
    repository = root / "demo-repository"
    source = repository / "src" / "api" / "health.py"
    source.parent.mkdir(parents=True)
    git(repository, "init", "-q")
    git(repository, "config", "user.name", "ReleaseProof Demo")
    git(repository, "config", "user.email", "release-proof@example.invalid")
    source.write_text(
        "def health_api():\n    return {'status': 'starting'}\n",
        encoding="utf-8",
    )
    git(repository, "add", "src/api/health.py")
    git(repository, "commit", "-q", "-m", "initial health endpoint")
    source.write_text(
        "def health_api():\n    return {'status': 'ok'}\n",
        encoding="utf-8",
    )
    git(repository, "add", "src/api/health.py")
    git(repository, "commit", "-q", "-m", "return healthy status")
    reports = repository / "reports"
    reports.mkdir()
    (reports / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="demo" tests="1" failures="0">
  <testcase classname="tests.test_health" name="test_health_api_returns_ok" time="0.01" />
</testsuite>
""",
        encoding="utf-8",
    )
    return repository


def domain_assessment_from_context(
    _system: str,
    user: str,
    _schema: type[BaseModel],
) -> dict[str, object]:
    """Select only evidence IDs that the bounded specialist prompt actually supplied."""
    evidence_ids = list(
        dict.fromkeys(
            re.findall(
                r"""["']evidence_id["']:\s*["'](agent-[0-9a-f]{16})["']""",
                user,
            )
        )
    )
    if not evidence_ids:
        raise RuntimeError("offline demo did not expose a bounded candidate evidence ID")
    return {
        "summary": "The bounded API evidence is ready for deterministic policy review.",
        "risks": [],
        "evidence_ids": evidence_ids[:1],
        "missing_evidence": [],
    }


def fake_model() -> FakeStructuredLLM:
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
            "NextAction": [
                {
                    "action": "call_tool",
                    "tool_name": "read_diff",
                    "arguments": {
                        "base_ref": "HEAD~1",
                        "head_ref": "HEAD",
                        "path": "src/api/health.py",
                    },
                    "target_criterion_ids": ["AC-001"],
                    "expected_evidence_kind": "diff",
                    "reason": "Read the changed implementation for AC-001.",
                },
                {
                    "action": "call_tool",
                    "tool_name": "read_test_report",
                    "arguments": {
                        "path": "reports/junit.xml",
                        "evidence_prefix": "demo-junit",
                    },
                    "target_criterion_ids": ["AC-001"],
                    "expected_evidence_kind": "test_result",
                    "reason": "Read the declared JUnit verification report.",
                },
                {
                    "action": "finish",
                    "reason": "Both implementation and verification evidence were collected.",
                },
            ],
            "DomainAssessmentDraft": domain_assessment_from_context,
        }
    )


def run_demo(work_dir: Path) -> dict[str, object]:
    demo_root = work_dir.resolve() / f"run-{uuid.uuid4().hex[:8]}"
    demo_root.mkdir(parents=True)
    repository = create_demo_repository(demo_root)
    project_root = Path(__file__).resolve().parents[1]
    llm = fake_model()
    nodes = WorkflowNodes(project_root / "skills", llm=llm)
    request = AnalysisRequest(
        repository_path=str(repository),
        base_ref="HEAD~1",
        head_ref="HEAD",
        requirement_source=RequirementSource(
            kind="inline",
            content="- Health API returns an ok status",
        ),
        report_paths=["reports/junit.xml"],
        continue_without_reports=True,
    )
    state: WorkflowState = {
        "run_id": f"offline-demo-{uuid.uuid4().hex[:8]}",
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
    state, interrupt = nodes.run_manual(state)
    if interrupt is not None:
        raise RuntimeError("self-contained demo unexpectedly requested input")
    report = ReleaseAssessment.model_validate(state["report"])
    paths = ReportWriter(demo_root / "reports").write(report)
    planner_actions = [
        {
            "action": item["action"],
            "tool_name": item.get("tool_name"),
            "status": item["status"],
        }
        for item in state["action_history"]
    ]
    expected_sequence = [
        ("call_tool", "read_diff", "executed"),
        ("call_tool", "read_test_report", "executed"),
        ("finish", None, "finished"),
    ]
    actual_sequence = [
        (item["action"], item["tool_name"], item["status"]) for item in planner_actions
    ]
    if actual_sequence != expected_sequence:
        raise RuntimeError(f"offline demo planner sequence drifted: {actual_sequence!r}")
    if report.missing_evidence:
        raise RuntimeError(
            f"offline demo unexpectedly ended with missing evidence: {report.missing_evidence!r}"
        )
    return {
        "mode": "offline FakeStructuredLLM through the real planner schema",
        "paid_api_calls": 0,
        "demo_root": str(demo_root),
        "self_checks": {
            "expected_planner_sequence": True,
            "no_missing_evidence": True,
            "paid_api_calls_are_zero": True,
        },
        "planner_actions": planner_actions,
        "tool_trace": [
            {
                "tool": item["tool"],
                "status": item["status"],
                "evidence_ids": item["evidence_ids"],
            }
            for item in state["trace"]
            if item["node"] == "validate_and_execute_readonly_tool"
        ],
        "evidence_items": len(report.evidence_index),
        "missing_evidence": report.missing_evidence,
        "recommendation": report.recommendation.value,
        "stop_reason": report.stop_reason,
        "reports": paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a self-contained, zero-cost bounded-agent demonstration."
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("runtime") / "bounded-agent-demo",
    )
    args = parser.parse_args()
    print(json.dumps(run_demo(args.work_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any, cast

from release_proof.adapters.llm import DeepSeekAnthropicClient, LLMDisabledError
from release_proof.config import Settings, get_settings
from release_proof.domain.models import (
    AnalysisRequest,
    AnalysisRun,
    InterruptPayload,
    ReleaseAssessment,
    ResumeRequest,
    RunStatus,
    TraceEvent,
)
from release_proof.graph.store import RunNotFoundError, SqliteRunStore
from release_proof.graph.workflow import WorkflowNodes, WorkflowState, build_langgraph
from release_proof.reporting import ReportWriter


def _now():
    return datetime.now(UTC)


class ReleaseProofService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        project_root: Path | None = None,
        prefer_langgraph: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_runtime_dirs()
        self.project_root = self._resolve_project_root(project_root)
        llm = None
        self.llm_error: str | None = None
        if not self.settings.release_proof_offline:
            try:
                llm = DeepSeekAnthropicClient(
                    api_key=self.settings.deepseek_api_key,
                    base_url=self.settings.deepseek_base_url,
                    model=self.settings.deepseek_model,
                    timeout_seconds=self.settings.llm_timeout_seconds,
                    max_retries=self.settings.llm_max_retries,
                )
            except LLMDisabledError as exc:
                self.llm_error = str(exc)
        self.nodes = WorkflowNodes(
            self.project_root / "skills",
            allowed_roots=self.settings.allowed_roots,
            llm=llm,
            max_llm_calls=self.settings.release_proof_max_llm_calls,
            max_output_tokens=self.settings.release_proof_max_output_tokens,
        )
        self.store = SqliteRunStore(self.settings.database_path)
        self.writer = ReportWriter(self.settings.generated_reports_dir)
        self.graph = None
        self._checkpoint_connection = None
        self.graph_error: str | None = None
        if prefer_langgraph:
            try:
                self.graph, self._checkpoint_connection = build_langgraph(
                    self.nodes, self.settings.checkpoint_path
                )
            except Exception as exc:  # dependency/config fallback remains explicit in health
                self.graph_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def _resolve_project_root(self, explicit: Path | None) -> Path:
        if explicit is not None:
            return explicit.resolve()
        if self.settings.release_proof_project_root is not None:
            return self.settings.release_proof_project_root.resolve()
        candidates = [Path.cwd(), Path(__file__).resolve().parents[3]]
        for candidate in candidates:
            if (candidate / "skills").is_dir() and (candidate / "evals" / "cases").is_dir():
                return candidate.resolve()
        return Path.cwd().resolve()

    def close(self) -> None:
        if self._checkpoint_connection is not None:
            self._checkpoint_connection.close()
            self._checkpoint_connection = None

    def _initial_state(self, run_id: str, request: AnalysisRequest) -> WorkflowState:
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
        }

    def start(self, request: AnalysisRequest) -> AnalysisRun:
        run_id = str(uuid.uuid4())
        thread_id = run_id
        run = AnalysisRun(
            run_id=run_id,
            thread_id=thread_id,
            status=RunStatus.RUNNING,
            request=request,
        )
        self.store.save(run, dict(self._initial_state(run_id, request)))
        try:
            if self.graph is not None:
                result = self.graph.invoke(
                    self._initial_state(run_id, request),
                    config={"configurable": {"thread_id": thread_id}},
                )
                return self._run_from_graph_result(run, result)
            state, interrupt = self.nodes.run_manual(self._initial_state(run_id, request))
            return self._finish_or_pause(run, state, interrupt)
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.errors = [f"{type(exc).__name__}: {str(exc)[:500]}"]
            run.updated_at = _now()
            self.store.save(run)
            return run

    def resume(self, run_id: str, resume: ResumeRequest) -> AnalysisRun:
        run = self.store.get(run_id)
        if run.status != RunStatus.AWAITING_INPUT:
            raise ValueError("only an awaiting_input run can be resumed")
        run.status = RunStatus.RUNNING
        run.interrupt = None
        run.updated_at = _now()
        self.store.save(run)
        try:
            if self.graph is not None:
                from langgraph.types import Command

                result = self.graph.invoke(
                    Command(resume=resume.model_dump(mode="json")),
                    config={"configurable": {"thread_id": run.thread_id}},
                )
                return self._run_from_graph_result(run, result)
            state = cast(
                WorkflowState,
                self.store.get_state(run_id) or self._initial_state(run_id, run.request),
            )
            state = self.nodes.apply_resume(state, resume)
            state = self.nodes.refresh_after_resume(state)
            reasons = self.nodes.missing_context_reasons(state)
            updated_request = AnalysisRequest.model_validate(state["request"])
            if reasons and not updated_request.continue_without_reports:
                payload = InterruptPayload(
                    run_id=run_id,
                    reasons=reasons,
                    requested_inputs=["valid report paths or explicit incomplete continuation"],
                )
                return self._finish_or_pause(run, state, payload)
            state = self.nodes.complete_without_interrupt(state)
            return self._finish_or_pause(run, state, None)
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.errors = [*run.errors, f"{type(exc).__name__}: {str(exc)[:500]}"]
            run.updated_at = _now()
            self.store.save(run)
            return run

    def _run_from_graph_result(self, run: AnalysisRun, result: dict[str, Any]) -> AnalysisRun:
        interrupts = result.get("__interrupt__", [])
        if interrupts:
            first = interrupts[0]
            raw = getattr(first, "value", first)
            payload = InterruptPayload.model_validate(raw)
            return self._finish_or_pause(run, cast(WorkflowState, result), payload)
        return self._finish_or_pause(run, cast(WorkflowState, result), None)

    def _finish_or_pause(
        self,
        run: AnalysisRun,
        state: WorkflowState,
        interrupt: InterruptPayload | None,
    ) -> AnalysisRun:
        run.trace = [TraceEvent.model_validate(item) for item in state.get("trace", [])]
        run.errors = list(state.get("errors", []))
        if interrupt is not None:
            run.status = RunStatus.AWAITING_INPUT
            run.interrupt = interrupt
        elif state.get("report"):
            run.status = RunStatus.COMPLETED
            raw_report = state.get("report")
            if raw_report is None:
                raise RuntimeError("workflow report disappeared during finalization")
            run.report = ReleaseAssessment.model_validate(raw_report)
            self.writer.write(run.report)
        else:
            run.status = RunStatus.FAILED
            run.errors.append("workflow ended without a report or interrupt")
        run.updated_at = _now()
        self.store.save(run, dict(state))
        return run

    def get(self, run_id: str) -> AnalysisRun:
        return self.store.get(run_id)

    def list(self, limit: int = 50) -> list[AnalysisRun]:
        return self.store.list(limit)

    def trace(self, run_id: str):
        return self.store.get(run_id).trace

    def health(self) -> dict[str, Any]:
        graph_installed = find_spec("langgraph") is not None
        workflow_degraded = graph_installed and self.graph is None
        llm_degraded = not self.settings.release_proof_offline and self.llm_error is not None
        assets_ready = (self.project_root / "skills").is_dir() and (
            self.project_root / "evals" / "cases"
        ).is_dir()
        return {
            "status": (
                "degraded" if workflow_degraded or llm_degraded or not assets_ready else "ok"
            ),
            "storage": self.store.health(),
            "workflow": "langgraph" if self.graph is not None else "offline-fallback",
            "workflow_warning": self.graph_error,
            "llm_mode": "offline" if self.settings.release_proof_offline else "configured-online",
            "llm_warning": self.llm_error,
            "project_assets": "ready" if assets_ready else "missing",
        }


__all__ = ["ReleaseProofService", "RunNotFoundError"]

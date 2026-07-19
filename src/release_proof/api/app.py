from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from release_proof.domain.models import AnalysisRequest, AnalysisRun, ResumeRequest
from release_proof.evaluation import EvaluationReport, EvaluationRunner
from release_proof.graph.service import ReleaseProofService, RunNotFoundError


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases_dir: str | None = None


def get_release_proof_service(request: Request) -> ReleaseProofService:
    return request.app.state.release_proof_service


ServiceDependency = Annotated[ReleaseProofService, Depends(get_release_proof_service)]


def create_app(service: ReleaseProofService | None = None) -> FastAPI:
    owns_service = service is None
    owned_service = service or ReleaseProofService()

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        yield
        if owns_service:
            owned_service.close()

    application = FastAPI(
        title="ReleaseProof API",
        version="0.1.0",
        description="Evidence-grounded release acceptance. It never approves a deployment.",
        lifespan=lifespan,
    )
    application.state.release_proof_service = owned_service

    @application.get("/health")
    def health(current: ServiceDependency):
        return current.health()

    @application.post(
        "/api/v1/analyses",
        response_model=AnalysisRun,
        status_code=status.HTTP_201_CREATED,
    )
    def create_analysis(
        request: AnalysisRequest, current: ServiceDependency
    ):
        run = current.start(request)
        if run.status.value == "failed":
            raise HTTPException(status_code=422, detail=run.model_dump(mode="json"))
        return run

    @application.get("/api/v1/analyses", response_model=list[AnalysisRun])
    def list_analyses(
        current: ServiceDependency,
        limit: int = Query(default=20, ge=1, le=100),
    ):
        return current.list(limit)

    @application.get("/api/v1/analyses/{run_id}", response_model=AnalysisRun)
    def get_analysis(run_id: str, current: ServiceDependency):
        try:
            return current.get(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="analysis not found") from exc

    @application.get("/api/v1/analyses/{run_id}/trace")
    def get_trace(run_id: str, current: ServiceDependency):
        try:
            return {"run_id": run_id, "trace": current.trace(run_id)}
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="analysis not found") from exc

    @application.post("/api/v1/analyses/{run_id}/resume", response_model=AnalysisRun)
    def resume_analysis(
        run_id: str,
        resume: ResumeRequest,
        current: ServiceDependency,
    ):
        try:
            return current.resume(run_id, resume)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="analysis not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/v1/skills")
    def list_skills(current: ServiceDependency):
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "version": skill.version,
            }
            for skill in current.nodes.skills.discover()
        ]

    @application.post("/api/v1/evaluations", response_model=EvaluationReport)
    def run_evaluation(
        request: EvaluationRequest, current: ServiceDependency
    ):
        cases_dir = (
            Path(request.cases_dir).resolve()
            if request.cases_dir
            else current.project_root / "evals" / "cases"
        )
        allowed_eval_root = (current.project_root / "evals").resolve()
        try:
            cases_dir.relative_to(allowed_eval_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="evaluation cases must be inside the project evals directory"
            ) from exc
        try:
            runner = EvaluationRunner()
            return runner.run(runner.load_cases(cases_dir))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return application


app = create_app()

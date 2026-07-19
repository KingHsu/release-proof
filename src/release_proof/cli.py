from __future__ import annotations

import argparse
import json
from pathlib import Path

from release_proof.domain.models import AnalysisRequest, RequirementSource, ResumeRequest
from release_proof.evaluation import EvaluationRunner
from release_proof.graph.service import ReleaseProofService


def _print(value) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release-proof", description="Read-only, evidence-grounded release acceptance"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="analyze a local Git change")
    analyze.add_argument("repository")
    analyze.add_argument("--base", default="HEAD~1")
    analyze.add_argument("--head", default="HEAD")
    analyze.add_argument("--requirement", required=True, help="inline acceptance checklist")
    analyze.add_argument("--report", action="append", default=[])
    analyze.add_argument("--ci-snapshot")
    analyze.add_argument("--mode", choices=["auto", "single", "multi"], default="auto")
    analyze.add_argument("--continue-without-reports", action="store_true")
    resume = sub.add_parser("resume", help="resume an interrupted analysis")
    resume.add_argument("run_id")
    resume.add_argument("--report", action="append", default=[])
    resume.add_argument("--ci-snapshot")
    resume.add_argument("--continue-without-reports", action="store_true")
    get = sub.add_parser("get", help="read a stored analysis")
    get.add_argument("run_id")
    sub.add_parser("doctor", help="show local runtime readiness without exposing secrets")
    evaluate = sub.add_parser("eval", help="run deterministic offline fixtures")
    evaluate.add_argument("--cases", default=None)
    serve = sub.add_parser("serve", help="start the FastAPI service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8002)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        import uvicorn

        uvicorn.run("release_proof.api.app:app", host=args.host, port=args.port, reload=False)
        return 0
    service = ReleaseProofService()
    try:
        if args.command == "analyze":
            request = AnalysisRequest(
                repository_path=args.repository,
                base_ref=args.base,
                head_ref=args.head,
                requirement_source=RequirementSource(kind="inline", content=args.requirement),
                report_paths=args.report,
                ci_snapshot_path=args.ci_snapshot,
                mode=args.mode,
                continue_without_reports=args.continue_without_reports,
            )
            _print(service.start(request))
        elif args.command == "resume":
            _print(
                service.resume(
                    args.run_id,
                    ResumeRequest(
                        report_paths=args.report,
                        ci_snapshot_path=args.ci_snapshot,
                        continue_without_reports=args.continue_without_reports,
                    ),
                )
            )
        elif args.command == "get":
            _print(service.get(args.run_id))
        elif args.command == "doctor":
            _print(
                {
                    **service.health(),
                    "python": "3.11+ required by pyproject.toml",
                    "git": "checked per analysis request",
                    "api_key_configured": bool(service.settings.deepseek_api_key),
                    "note": "key value is never printed; offline tests do not use it",
                }
            )
        elif args.command == "eval":
            cases_dir = Path(args.cases) if args.cases else service.project_root / "evals" / "cases"
            runner = EvaluationRunner()
            _print(runner.run(runner.load_cases(cases_dir)))
    finally:
        service.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


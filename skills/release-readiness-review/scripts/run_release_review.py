#!/usr/bin/env python3
"""Run ReleaseProof through its CLI and verify the generated report artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--base", default="HEAD~1")
    parser.add_argument("--head", default="HEAD")
    requirements = parser.add_mutually_exclusive_group(required=True)
    requirements.add_argument("--requirement-file", type=Path)
    requirements.add_argument("--requirement")
    parser.add_argument("--report", action="append", default=[])
    parser.add_argument("--ci-snapshot")
    parser.add_argument("--mode", choices=["auto", "single", "multi"], default="auto")
    parser.add_argument("--continue-without-reports", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path)
    parser.add_argument(
        "--cli",
        default=os.environ.get("RELEASE_PROOF_CLI", "release-proof"),
        help="Installed ReleaseProof executable. It is invoked directly, never through a shell.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser


def _load_json(text: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} was not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _safe_run_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("ReleaseProof output did not contain a run_id")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if any(char not in allowed for char in value):
        raise ValueError("ReleaseProof output contained an unsafe run_id")
    return value


def _write_inline_requirement(text: str, staging_dir: Path) -> Path:
    if not text.strip():
        raise ValueError("inline requirement must not be empty")
    staging_dir.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix="release-proof-requirements-",
        suffix=".md",
        dir=staging_dir,
        text=True,
    )
    os.close(descriptor)
    path = Path(raw_path)
    path.write_text(text, encoding="utf-8")
    return path


def execute(
    args: argparse.Namespace,
    *,
    runner: Runner = subprocess.run,
) -> tuple[int, dict[str, Any]]:
    repository = args.repository.resolve()
    if not repository.is_dir():
        raise ValueError(f"repository does not exist: {repository}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = output_dir / "runtime"
    staging_dir = (args.staging_dir or output_dir / "staging").resolve()

    temporary_requirement: Path | None = None
    if args.requirement_file is not None:
        requirement_file = args.requirement_file.resolve()
        if not requirement_file.is_file():
            raise ValueError(f"requirement file does not exist: {requirement_file}")
    else:
        temporary_requirement = _write_inline_requirement(args.requirement, staging_dir)
        requirement_file = temporary_requirement

    command: list[str] = [
        args.cli,
        "analyze",
        str(repository),
        "--base",
        args.base,
        "--head",
        args.head,
        "--requirement-file",
        str(requirement_file),
        "--mode",
        args.mode,
    ]
    for report in args.report:
        command.extend(["--report", report])
    if args.ci_snapshot:
        command.extend(["--ci-snapshot", args.ci_snapshot])
    if args.continue_without_reports:
        command.append("--continue-without-reports")

    environment = os.environ.copy()
    environment["RELEASE_PROOF_DATA_DIR"] = str(runtime_dir)
    skill_project_root = Path(__file__).resolve().parents[3]
    if (skill_project_root / "skills").is_dir() and (skill_project_root / "evals").is_dir():
        environment.setdefault("RELEASE_PROOF_PROJECT_ROOT", str(skill_project_root))
    allowed_roots = [
        item.strip()
        for item in environment.get("RELEASE_PROOF_ALLOWED_ROOTS", "").split(";")
        if item.strip()
    ]
    if str(repository) not in allowed_roots:
        allowed_roots.append(str(repository))
    environment["RELEASE_PROOF_ALLOWED_ROOTS"] = ";".join(allowed_roots)

    try:
        completed = runner(
            command,
            cwd=skill_project_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=args.timeout_seconds,
            shell=False,
        )
    finally:
        if temporary_requirement is not None:
            temporary_requirement.unlink(missing_ok=True)

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        return 1, {
            "status": "execution_failed",
            "error": detail or f"release-proof exited with {completed.returncode}",
        }

    run = _load_json(completed.stdout, label="ReleaseProof stdout")
    run_id = _safe_run_id(run.get("run_id"))
    run_path = output_dir / "analysis-run.json"
    run_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")

    summary: dict[str, Any] = {
        "status": run.get("status"),
        "run_id": run_id,
        "analysis_run": str(run_path),
        "recommendation": None,
        "report_json": None,
        "report_markdown": None,
        "interrupt": run.get("interrupt"),
        "errors": run.get("errors", []),
        "notice": "This review never approves, merges, or deploys a release.",
    }

    embedded_report = run.get("report")
    if embedded_report is not None:
        if not isinstance(embedded_report, dict):
            raise ValueError("embedded ReleaseProof report must be a JSON object")
        json_path = runtime_dir / "reports" / f"{run_id}.json"
        markdown_path = runtime_dir / "reports" / f"{run_id}.md"
        if not json_path.is_file() or not markdown_path.is_file():
            raise ValueError("ReleaseProof completed without both generated report artifacts")
        report = _load_json(json_path.read_text(encoding="utf-8"), label="generated report")
        markdown = markdown_path.read_text(encoding="utf-8")
        if report.get("run_id") != run_id or embedded_report.get("run_id") != run_id:
            raise ValueError("run_id mismatch between CLI output and generated report")
        if not markdown.strip() or run_id not in markdown:
            raise ValueError("generated Markdown report is empty or belongs to another run")
        if report.get("recommendation") != embedded_report.get("recommendation"):
            raise ValueError("recommendation mismatch between CLI output and generated report")
        summary.update(
            {
                "recommendation": report.get("recommendation"),
                "report_json": str(json_path),
                "report_markdown": str(markdown_path),
                "criteria_total": len(report.get("acceptance_matrix", [])),
                "risks_total": len(report.get("domain_risks", [])),
                "human_checks_total": len(report.get("human_checks", [])),
            }
        )

    exit_code = 2 if run.get("status") == "failed" else 0
    return exit_code, summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        exit_code, summary = execute(args)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        exit_code, summary = 1, {"status": "wrapper_failed", "error": str(exc)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    return completed.stdout


def make_git_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    run_git(root, "init", "-q")
    run_git(root, "config", "user.name", "ReleaseProof Test")
    run_git(root, "config", "user.email", "release-proof@example.invalid")
    (root / "src" / "api").mkdir(parents=True)
    health = root / "src" / "api" / "health.py"
    health.write_text("def health_api():\n    return {'status': 'starting'}\n", encoding="utf-8")
    run_git(root, "add", "src/api/health.py")
    run_git(root, "commit", "-q", "-m", "initial health endpoint")
    health.write_text("def health_api():\n    return {'status': 'ok'}\n", encoding="utf-8")
    run_git(root, "add", "src/api/health.py")
    run_git(root, "commit", "-q", "-m", "return ok health status")
    return root


def write_junit(root: Path) -> Path:
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    path = reports / "junit.xml"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="release-proof" tests="2" failures="1">
  <testcase classname="tests.test_health" name="test_health_api_returns_ok" time="0.01" />
  <testcase classname="tests.test_other" name="test_other_failure" time="0.01">
    <failure message="expected failure" />
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    return path


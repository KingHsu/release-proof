from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = PROJECT_ROOT / "runtime" / "demo-repo"


def git(*args: str) -> None:
    subprocess.run(
        ["git", "-C", str(DEMO_ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )


def main() -> int:
    resolved = DEMO_ROOT.resolve()
    runtime = (PROJECT_ROOT / "runtime").resolve()
    resolved.relative_to(runtime)
    if resolved.exists():
        shutil.rmtree(resolved)
    (resolved / "src" / "api").mkdir(parents=True)
    git("init", "-q")
    git("config", "user.name", "ReleaseProof Demo")
    git("config", "user.email", "demo@example.invalid")
    health = resolved / "src" / "api" / "health.py"
    health.write_text("def health_api():\n    return {'status': 'starting'}\n", encoding="utf-8")
    git("add", "src/api/health.py")
    git("commit", "-q", "-m", "initial health endpoint")
    health.write_text("def health_api():\n    return {'status': 'ok'}\n", encoding="utf-8")
    git("add", "src/api/health.py")
    git("commit", "-q", "-m", "return ok health status")
    reports = resolved / "reports"
    reports.mkdir()
    (reports / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="demo" tests="1" failures="0">
  <testcase classname="tests.test_health" name="test_health_api_returns_ok" time="0.01" />
</testsuite>
""",
        encoding="utf-8",
    )
    print(f"Demo repository created: {resolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

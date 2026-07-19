from __future__ import annotations

import json
from pathlib import Path

from release_proof.evaluation import EvaluationRunner


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    runner = EvaluationRunner()
    report = runner.run(runner.load_cases(root / "evals" / "cases"))
    target = root / "reports" / "generated" / "offline-evaluation.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
"""Validate a release-evidence manifest without approving or changing a release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

COMPLETE_CHECK_STATUSES = {"passed", "present", "verified", "not_applicable"}
CRITERION_STATUSES = {"verified", "partial", "missing", "not_applicable"}


def issue(code: str, location: str, message: str) -> dict[str, str]:
    return {"code": code, "location": location, "message": message}


def validate(manifest: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    criteria = manifest.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        issues.append(
            issue(
                "NO_ACCEPTANCE_CRITERIA",
                "acceptance_criteria",
                "At least one criterion is required.",
            )
        )
    else:
        seen_ids: set[str] = set()
        for index, criterion in enumerate(criteria):
            location = f"acceptance_criteria[{index}]"
            if not isinstance(criterion, dict):
                issues.append(issue("INVALID_CRITERION", location, "Criterion must be an object."))
                continue
            criterion_id = criterion.get("id")
            if not isinstance(criterion_id, str) or not criterion_id.strip():
                issues.append(
                    issue("MISSING_CRITERION_ID", location, "Criterion needs a stable id.")
                )
            elif criterion_id in seen_ids:
                issues.append(
                    issue(
                        "DUPLICATE_CRITERION_ID",
                        location,
                        f"Duplicate id: {criterion_id}",
                    )
                )
            else:
                seen_ids.add(criterion_id)
            status = criterion.get("status")
            if status not in CRITERION_STATUSES:
                issues.append(
                    issue(
                        "INVALID_CRITERION_STATUS",
                        location,
                        "Criterion status is invalid.",
                    )
                )
                continue
            evidence = criterion.get("evidence", [])
            valid_evidence = isinstance(evidence, list) and any(
                isinstance(item, dict)
                and isinstance(item.get("ref"), str)
                and item["ref"].strip()
                for item in evidence
            )
            if status == "verified" and not valid_evidence:
                issues.append(
                    issue(
                        "VERIFIED_WITHOUT_EVIDENCE",
                        location,
                        "Verified criterion has no concrete evidence.",
                    )
                )
            elif status in {"partial", "missing"}:
                issues.append(issue("CRITERION_INCOMPLETE", location, f"Criterion is {status}."))
            elif status == "not_applicable" and not valid_evidence:
                issues.append(
                    issue(
                        "NOT_APPLICABLE_WITHOUT_REASON",
                        location,
                        "Not-applicable criterion needs an evidence reference explaining why.",
                    )
                )

    required_checks = manifest.get("required_checks", [])
    checks = manifest.get("checks", {})
    if not isinstance(required_checks, list) or not all(
        isinstance(item, str) for item in required_checks
    ):
        issues.append(
            issue(
                "INVALID_REQUIRED_CHECKS",
                "required_checks",
                "Required checks must be a list of names.",
            )
        )
    elif not isinstance(checks, dict):
        issues.append(issue("INVALID_CHECKS", "checks", "Checks must be an object."))
    else:
        for check_name in required_checks:
            check = checks.get(check_name)
            location = f"checks.{check_name}"
            if not isinstance(check, dict):
                issues.append(
                    issue("MISSING_REQUIRED_CHECK", location, "Required check is missing.")
                )
                continue
            if check.get("status") not in COMPLETE_CHECK_STATUSES:
                issues.append(
                    issue(
                        "REQUIRED_CHECK_FAILED",
                        location,
                        "Required check is not complete.",
                    )
                )
            if not isinstance(check.get("ref"), str) or not check["ref"].strip():
                issues.append(
                    issue(
                        "CHECK_WITHOUT_EVIDENCE",
                        location,
                        "Required check needs an evidence reference.",
                    )
                )

    risks = manifest.get("risks", [])
    if not isinstance(risks, list):
        issues.append(issue("INVALID_RISKS", "risks", "Risks must be a list."))
    else:
        for index, risk in enumerate(risks):
            if isinstance(risk, dict) and risk.get("severity") in {"critical", "high"}:
                issues.append(
                    issue(
                        "BLOCKING_RISK",
                        f"risks[{index}]",
                        "Critical or high risk remains open.",
                    )
                )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        value = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("manifest must be a JSON object")
        issues = validate(value)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    ready = not issues
    result = {
        "status": "ready_for_human_review" if ready else "not_ready",
        "ready": ready,
        "release_id": value.get("release_id"),
        "blocking_issues": issues,
        "notice": "This result is non-binding and never approves or deploys a release.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())

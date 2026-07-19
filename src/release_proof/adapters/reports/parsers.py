from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as SafeET

from release_proof.domain.models import EvidenceItem, EvidenceKind
from release_proof.tools.policy import ToolPolicy


class ReportParseError(ValueError):
    pass


def _safe_status(case: Any) -> str:
    if case.find("failure") is not None:
        return "failed"
    if case.find("error") is not None:
        return "error"
    if case.find("skipped") is not None:
        return "skipped"
    return "passed"


@dataclass
class ReportCollector:
    policy: ToolPolicy

    def read(self, report_path: str, *, evidence_prefix: str) -> list[EvidenceItem]:
        path = self.policy.validate_external_report(report_path)
        if path.suffix.lower() == ".xml":
            root = SafeET.parse(path).getroot()
            if root is None:
                raise ReportParseError("XML report has no root element")
            tag = root.tag.lower().split("}")[-1]
            if tag in {"testsuite", "testsuites"}:
                return self._read_junit(path, root, evidence_prefix)
            if tag == "coverage":
                return self._read_coverage_xml(path, root, evidence_prefix)
            raise ReportParseError("unknown XML report root")
        return self._read_json(path, evidence_prefix)

    def _read_junit(self, path: Path, root: Any, prefix: str) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for index, case in enumerate(root.iter("testcase")):
            name = case.attrib.get("name", "unnamed")
            classname = case.attrib.get("classname", "")
            status = _safe_status(case)
            duration = case.attrib.get("time", "")
            content = f"{classname}::{name} status={status} duration={duration}"
            items.append(
                EvidenceItem.from_observation(
                    evidence_id=f"{prefix}-test-{index + 1}",
                    kind=EvidenceKind.TEST_RESULT,
                    source_uri=path.as_uri(),
                    locator=f"testcase[{index}]",
                    content=content,
                    observed_by="read_test_report:v1",
                    metadata={"name": name, "classname": classname, "status": status},
                )
            )
        if not items:
            raise ReportParseError("JUnit report has no testcase elements")
        return items

    def _read_coverage_xml(self, path: Path, root: Any, prefix: str) -> list[EvidenceItem]:
        line_rate = root.attrib.get("line-rate")
        branch_rate = root.attrib.get("branch-rate")
        content = f"coverage line_rate={line_rate or 'unknown'} branch_rate={branch_rate or 'unknown'}"
        return [
            EvidenceItem.from_observation(
                evidence_id=f"{prefix}-coverage-1",
                kind=EvidenceKind.COVERAGE,
                source_uri=path.as_uri(),
                locator="coverage",
                content=content,
                observed_by="read_coverage:v1",
                metadata={"line_rate": line_rate, "branch_rate": branch_rate},
            )
        ]

    def _read_json(self, path: Path, prefix: str) -> list[EvidenceItem]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReportParseError("invalid JSON report") from exc
        if isinstance(payload, dict) and "jobs" in payload:
            return self._read_ci_jobs(path, payload, prefix)
        if isinstance(payload, dict) and ("totals" in payload or "files" in payload):
            totals = payload.get("totals", payload.get("total", {}))
            content = f"coverage totals={json.dumps(totals, ensure_ascii=False, sort_keys=True)[:2000]}"
            return [
                EvidenceItem.from_observation(
                    evidence_id=f"{prefix}-coverage-1",
                    kind=EvidenceKind.COVERAGE,
                    source_uri=path.as_uri(),
                    locator="totals",
                    content=content,
                    observed_by="read_coverage:v1",
                    metadata={"totals": totals},
                )
            ]
        raise ReportParseError("JSON is neither a CI snapshot nor coverage report")

    def read_ci_snapshot(self, snapshot_path: str, *, evidence_prefix: str) -> list[EvidenceItem]:
        path = self.policy.validate_external_report(snapshot_path)
        if path.suffix.lower() != ".json":
            raise ReportParseError("CI snapshot must be JSON")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReportParseError("invalid CI snapshot") from exc
        return self._read_ci_jobs(path, payload, evidence_prefix)

    def _read_ci_jobs(self, path: Path, payload: dict[str, Any], prefix: str) -> list[EvidenceItem]:
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ReportParseError("CI snapshot needs a jobs array")
        items: list[EvidenceItem] = []
        for index, job in enumerate(jobs[:100]):
            if not isinstance(job, dict):
                continue
            name = str(job.get("name", f"job-{index + 1}"))
            status = str(job.get("status", "unknown"))
            conclusion = str(job.get("conclusion", status))
            content = f"CI job {name}: status={status} conclusion={conclusion}"
            items.append(
                EvidenceItem.from_observation(
                    evidence_id=f"{prefix}-ci-{index + 1}",
                    kind=EvidenceKind.CI,
                    source_uri=path.as_uri(),
                    locator=f"jobs[{index}]",
                    content=content,
                    observed_by="read_ci_summary:v1",
                    metadata={"name": name, "status": status, "conclusion": conclusion},
                )
            )
        if not items:
            raise ReportParseError("CI snapshot contains no jobs")
        return items

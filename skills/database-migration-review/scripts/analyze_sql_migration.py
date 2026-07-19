#!/usr/bin/env python3
"""Statically flag a small, explainable set of risky PostgreSQL migration patterns."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

RULES: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "critical",
        "DROP_TABLE",
        "Dropping a table can cause irreversible data loss.",
        re.compile(r"\bdrop\s+table\b", re.I),
    ),
    (
        "critical",
        "TRUNCATE",
        "Truncating a table removes all rows.",
        re.compile(r"\btruncate(?:\s+table)?\b", re.I),
    ),
    (
        "critical",
        "DROP_COLUMN",
        "Dropping a column can cause irreversible data loss.",
        re.compile(r"\bdrop\s+column\b", re.I),
    ),
    (
        "high",
        "ALTER_COLUMN_TYPE",
        "Changing a column type can rewrite or lock a table.",
        re.compile(r"\balter\s+column\b[\s\S]*?\btype\b", re.I),
    ),
    (
        "high",
        "SET_NOT_NULL",
        "Adding NOT NULL can scan or lock a populated table.",
        re.compile(r"\bset\s+not\s+null\b", re.I),
    ),
    (
        "high",
        "ADD_NOT_NULL_WITHOUT_DEFAULT",
        "A required column needs a staged population plan.",
        re.compile(r"\badd\s+column\b(?:(?!\bdefault\b)[\s\S])*?\bnot\s+null\b", re.I),
    ),
)


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def analyze(text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for severity, code, message, pattern in RULES:
        for match in pattern.finditer(text):
            findings.append(
                {
                    "severity": severity,
                    "code": code,
                    "line": line_number(text, match.start()),
                    "message": message,
                }
            )

    unbounded_change = re.compile(
        r"(?:^|;)\s*(delete\s+from|update\s+)\b[^;]*", re.I | re.M
    )
    for statement_match in unbounded_change.finditer(text):
        statement = statement_match.group(0).lstrip("; \t\r\n")
        if not re.search(r"\bwhere\b", statement, re.I):
            kind = "DELETE" if statement.lower().startswith("delete") else "UPDATE"
            findings.append(
                {
                    "severity": "high",
                    "code": f"UNBOUNDED_{kind}",
                    "line": line_number(text, statement_match.start(1)),
                    "message": (
                        f"{kind} has no WHERE clause; require explicit "
                        "bounded-change evidence."
                    ),
                }
            )

    for match in re.finditer(r"\bcreate\s+(?:unique\s+)?index\b", text, re.I):
        statement_end = text.find(";", match.start())
        statement = text[match.start() : statement_end if statement_end != -1 else len(text)]
        if not re.search(r"\bconcurrently\b", statement, re.I):
            findings.append(
                {
                    "severity": "medium",
                    "code": "INDEX_NOT_CONCURRENT",
                    "line": line_number(text, match.start()),
                    "message": "Index creation is not CONCURRENTLY; review write-lock impact.",
                }
            )

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        findings,
        key=lambda item: (severity_order[item["severity"]], item["line"], item["code"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("migration", type=Path)
    args = parser.parse_args()
    try:
        text = args.migration.read_text(encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    findings = analyze(text)
    blocking = any(item["severity"] in {"critical", "high"} for item in findings)
    result = {
        "status": "failed" if blocking else ("needs_human_review" if findings else "passed"),
        "finding_count": len(findings),
        "findings": findings,
        "limitations": [
            "Static analysis cannot estimate locks, duration, row counts, or production traffic.",
            "Dynamic SQL and database-specific runtime behavior require human review.",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only static scan for recurring Java microservice release-risk patterns."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_SUFFIXES = {
    ".java",
    ".sql",
    ".xml",
    ".yml",
    ".yaml",
    ".properties",
    ".conf",
    ".json",
}
MAX_FILES = 500
MAX_FILE_BYTES = 2_000_000
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class SourceFile:
    relative: str
    path: Path
    text: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--base", default="HEAD~1")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--git-timeout-seconds", type=int, default=15)
    return parser


def _validate_ref(value: str, label: str) -> str:
    if not value or value.startswith("-") or "\x00" in value:
        raise ValueError(f"unsafe {label}")
    return value


def _resolve_commit(root: Path, ref: str, label: str, timeout: int) -> str:
    validated = _validate_ref(ref, label)
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{validated}^{{commit}}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        shell=False,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-fA-F]{40,64}", commit) is None:
        raise ValueError((result.stderr or f"cannot resolve {label}").strip()[-1000:])
    return commit


def _git_changed_files(root: Path, base: str, head: str, timeout: int) -> list[str]:
    command = [
        "git",
        "-c",
        "core.quotepath=false",
        "diff",
        "--name-only",
        "--diff-filter=ACMRT",
        base,
        head,
        "--",
    ]
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        shell=False,
    )
    if result.returncode != 0:
        raise ValueError((result.stderr or "git diff failed").strip()[-1000:])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _candidate_path(root: Path, raw: str) -> tuple[Path, str]:
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ValueError("changed path contains a forbidden control character")
    candidate = (root / raw).resolve()
    relative = raw.replace("\\", "/")
    return candidate, relative


def _load_worktree_sources(
    root: Path, relative_paths: Iterable[str]
) -> tuple[list[SourceFile], list[dict[str, str]]]:
    sources: list[SourceFile] = []
    skipped: list[dict[str, str]] = []
    unique_paths = list(dict.fromkeys(relative_paths))
    if len(unique_paths) > MAX_FILES:
        raise ValueError(f"changed file count exceeds {MAX_FILES}")
    for raw in unique_paths:
        candidate, relative = _candidate_path(root, raw)
        if not _within(root, candidate):
            skipped.append({"file": relative, "reason": "outside_repository"})
            continue
        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            skipped.append({"file": relative, "reason": "unsupported_extension"})
            continue
        if not candidate.is_file():
            skipped.append({"file": relative, "reason": "missing_or_not_file"})
            continue
        if candidate.stat().st_size > MAX_FILE_BYTES:
            skipped.append({"file": relative, "reason": "file_too_large"})
            continue
        sources.append(
            SourceFile(
                relative=candidate.relative_to(root).as_posix(),
                path=candidate,
                text=candidate.read_text(encoding="utf-8", errors="replace"),
            )
        )
    return sources, skipped


def _load_git_sources(
    root: Path,
    relative_paths: Iterable[str],
    head_commit: str,
    timeout: int,
) -> tuple[list[SourceFile], list[dict[str, str]]]:
    sources: list[SourceFile] = []
    skipped: list[dict[str, str]] = []
    unique_paths = list(dict.fromkeys(relative_paths))
    if len(unique_paths) > MAX_FILES:
        raise ValueError(f"changed file count exceeds {MAX_FILES}")
    for raw in unique_paths:
        candidate, relative = _candidate_path(root, raw)
        if not _within(root, candidate):
            skipped.append({"file": relative, "reason": "outside_repository"})
            continue
        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            skipped.append({"file": relative, "reason": "unsupported_extension"})
            continue
        object_spec = f"{head_commit}:{relative}"
        size_result = subprocess.run(
            ["git", "cat-file", "-s", object_spec],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            shell=False,
        )
        try:
            object_size = int(size_result.stdout.strip())
        except ValueError:
            object_size = -1
        if size_result.returncode != 0 or object_size < 0:
            skipped.append({"file": relative, "reason": "missing_at_head"})
            continue
        if object_size > MAX_FILE_BYTES:
            skipped.append({"file": relative, "reason": "file_too_large"})
            continue
        content_result = subprocess.run(
            ["git", "show", object_spec],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        if content_result.returncode != 0:
            skipped.append({"file": relative, "reason": "git_show_failed"})
            continue
        sources.append(
            SourceFile(relative=relative, path=candidate, text=content_result.stdout)
        )
    return sources, skipped


def _finding(
    code: str,
    severity: str,
    source: SourceFile,
    line: int,
    message: str,
    evidence_pattern: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "file": source.relative,
        "line": line,
        "message": message,
        "evidence_pattern": evidence_pattern,
    }


def _first_line(text: str, pattern: str) -> int:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return text.count("\n", 0, match.start()) + 1 if match else 1


def scan(sources: list[SourceFile]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for source in sources:
        text = source.text
        lowered_path = source.relative.lower()

        if re.search(r"\bNOT\s+IN\s*\(", text, re.IGNORECASE):
            findings.append(
                _finding(
                    "ORACLE_NULL_NOT_IN_REVIEW",
                    "high",
                    source,
                    _first_line(text, r"\bNOT\s+IN\s*\("),
                    "Check nullable operands and subquery values; Oracle three-valued logic can silently exclude rows.",
                    "NOT IN",
                )
            )
        if re.search(r"\bTO_DATE\s*\(\s*SYSDATE\b", text, re.IGNORECASE):
            findings.append(
                _finding(
                    "ORACLE_DATE_INDEX_REVIEW",
                    "high",
                    source,
                    _first_line(text, r"\bTO_DATE\s*\(\s*SYSDATE\b"),
                    "Check implicit conversion and index usage; compare compatible date types explicitly.",
                    "TO_DATE(SYSDATE)",
                )
            )
        if source.path.suffix.lower() == ".java" and re.search(r"\benum\s+\w+", text):
            findings.append(
                _finding(
                    "ENUM_MAPPING_COVERAGE",
                    "medium",
                    source,
                    _first_line(text, r"\benum\s+\w+"),
                    "Trace wire/database values, mappings, switches, and unknown-value behavior across services.",
                    "enum declaration",
                )
            )

        config_signal = (
            source.path.suffix.lower() in {".yml", ".yaml", ".properties", ".conf"}
            or re.search(r"@(Value|ConfigurationProperties|RefreshScope)\b", text) is not None
        )
        if config_signal:
            findings.append(
                _finding(
                    "CONFIG_DEFAULT_AND_ROLLOUT",
                    "medium",
                    source,
                    _first_line(text, r"@(Value|ConfigurationProperties|RefreshScope)\b"),
                    "Verify ownership, defaults, validation, environment overrides, refresh behavior, gray rollout, and rollback value.",
                    "configuration change",
                )
            )

        async_signal = re.search(
            r"@(Scheduled|KafkaListener|RabbitListener|JmsListener|XxlJob)\b|"
            r"\b(insertOrUpdateBatch|saveBatch|batchInsert|batchUpdate)\b",
            text,
            re.IGNORECASE,
        )
        write_signal = re.search(
            r"\b(insert|save|update|upsert|merge|persist)\w*\s*\(",
            text,
            re.IGNORECASE,
        )
        if async_signal and write_signal:
            findings.append(
                _finding(
                    "BATCH_IDEMPOTENCY_REVIEW",
                    "high",
                    source,
                    text.count("\n", 0, async_signal.start()) + 1,
                    "Verify stable business keys, null/blank handling, uniqueness, retries, duplicate delivery, and partial failure.",
                    "scheduled/message/batch write",
                )
            )

        remote_signal = re.search(
            r"@(FeignClient|DubboReference|Reference)\b|\b(RestTemplate|WebClient)\b",
            text,
        )
        transaction_signal = re.search(r"@Transactional\b", text)
        if remote_signal and transaction_signal:
            findings.append(
                _finding(
                    "REMOTE_CALL_IN_LOCAL_TRANSACTION",
                    "high",
                    source,
                    text.count("\n", 0, remote_signal.start()) + 1,
                    "A local transaction does not make a remote call atomic; verify timeout, retry, compensation, and observable partial states.",
                    "remote call plus @Transactional",
                )
            )

        pagination_tokens = re.findall(
            r"\b(PageHelper|PageRequest|ROWNUM|OFFSET|FETCH\s+NEXT|LIMIT)\b",
            text,
            re.IGNORECASE,
        )
        merge_signal = re.search(r"\b(addAll|concat|union|merge)\b", text, re.IGNORECASE)
        if len(pagination_tokens) >= 2 and merge_signal:
            findings.append(
                _finding(
                    "MULTI_SOURCE_PAGINATION_REVIEW",
                    "high",
                    source,
                    text.count("\n", 0, merge_signal.start()) + 1,
                    "Independent source pagination followed by merging may produce empty or inconsistent global pages.",
                    "multiple pagination operations plus merge",
                )
            )

        dto_path = re.search(r"(^|/)(dto|request|response|contract|api)(/|$)", lowered_path)
        dto_type = re.search(
            r"\b(class|record)\s+\w*(Request|Response|Dto|DTO|Command|Event)\b",
            text,
        )
        if dto_path or dto_type:
            findings.append(
                _finding(
                    "CROSS_SERVICE_FIELD_PROPAGATION",
                    "medium",
                    source,
                    _first_line(text, r"\b(class|record)\s+\w+"),
                    "Trace changed fields through contracts, serializers, adapters, mappings, persistence, and downstream consumers.",
                    "boundary DTO or contract",
                )
            )

        if re.search(r"(^|/)(deploy|deployment|helm|k8s|config)(/|$)", lowered_path):
            findings.append(
                _finding(
                    "GRAY_RELEASE_AND_ROLLBACK",
                    "medium",
                    source,
                    1,
                    "Require compatibility order, observation window, abort threshold, owner, and rollback constraints.",
                    "deployment/configuration artifact",
                )
            )

    deduplicated = {
        (item["code"], item["file"], item["line"]): item for item in findings
    }
    return sorted(
        deduplicated.values(),
        key=lambda item: (
            -SEVERITY_ORDER[item["severity"]],
            item["file"],
            item["line"],
            item["code"],
        ),
    )


def main() -> int:
    args = _parser().parse_args()
    try:
        root = args.repository.resolve()
        if not root.is_dir():
            raise ValueError(f"repository does not exist: {root}")
        if args.file:
            changed_files = args.file
            sources, skipped = _load_worktree_sources(root, changed_files)
            resolved_base = None
            resolved_head = None
        else:
            resolved_base = _resolve_commit(
                root, args.base, "base ref", args.git_timeout_seconds
            )
            resolved_head = _resolve_commit(
                root, args.head, "head ref", args.git_timeout_seconds
            )
            changed_files = _git_changed_files(
                root,
                resolved_base,
                resolved_head,
                args.git_timeout_seconds,
            )
            sources, skipped = _load_git_sources(
                root,
                changed_files,
                resolved_head,
                args.git_timeout_seconds,
            )
        findings = scan(sources)
        has_high = any(item["severity"] in {"high", "critical"} for item in findings)
        payload = {
            "status": "needs_human_review" if findings else "no_static_findings",
            "base": args.base if not args.file else None,
            "head": args.head if not args.file else None,
            "resolved_base": resolved_base,
            "resolved_head": resolved_head,
            "changed_files_total": len(changed_files),
            "files_scanned": [source.relative for source in sources],
            "skipped": skipped,
            "findings": findings,
            "notice": (
                "Static findings require evidence-backed human review. "
                "No finding or zero exit status is not release approval."
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2 if has_high else 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

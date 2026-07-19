#!/usr/bin/env python3
"""Detect a deliberately small, explainable set of OpenAPI breaking changes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as json_error:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ValueError(
                f"{path} is not JSON; install PyYAML to read YAML OpenAPI documents"
            ) from exc
        try:
            value = yaml.safe_load(text)
        except Exception as exc:  # PyYAML exposes several parser exception types.
            raise ValueError(f"cannot parse {path}: {exc}") from json_error
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an OpenAPI object")
    return value


def finding(code: str, location: str, message: str) -> dict[str, str]:
    return {
        "severity": "breaking",
        "code": code,
        "location": location,
        "message": message,
    }


def parameters(
    path_item: dict[str, Any], operation: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in (path_item.get("parameters", []), operation.get("parameters", [])):
        if not isinstance(candidate, list):
            continue
        for parameter in candidate:
            if not isinstance(parameter, dict) or "$ref" in parameter:
                continue
            name = parameter.get("name")
            location = parameter.get("in")
            if isinstance(name, str) and isinstance(location, str):
                merged[(location, name)] = parameter
    return merged


def detect_breaks(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    old_paths = old.get("paths", {})
    new_paths = new.get("paths", {})
    if not isinstance(old_paths, dict) or not isinstance(new_paths, dict):
        raise ValueError("both documents must contain an object at 'paths'")

    for path, old_path_item in old_paths.items():
        if not isinstance(old_path_item, dict):
            continue
        new_path_item = new_paths.get(path)
        if not isinstance(new_path_item, dict):
            findings.append(finding("PATH_REMOVED", path, "Existing API path was removed."))
            continue

        for method, old_operation in old_path_item.items():
            method_lower = method.lower()
            if method_lower not in HTTP_METHODS or not isinstance(old_operation, dict):
                continue
            new_operation = new_path_item.get(method_lower)
            location = f"{method_lower.upper()} {path}"
            if not isinstance(new_operation, dict):
                findings.append(
                    finding("OPERATION_REMOVED", location, "Existing HTTP operation was removed.")
                )
                continue

            old_parameters = parameters(old_path_item, old_operation)
            new_parameters = parameters(new_path_item, new_operation)
            for key, new_parameter in new_parameters.items():
                old_parameter = old_parameters.get(key)
                if new_parameter.get("required") is True and (
                    old_parameter is None or old_parameter.get("required") is not True
                ):
                    param_in, param_name = key
                    findings.append(
                        finding(
                            "REQUIRED_PARAMETER_ADDED",
                            f"{location} parameter {param_in}:{param_name}",
                            "A new parameter is required or an optional parameter became required.",
                        )
                    )

            old_body = old_operation.get("requestBody", {})
            new_body = new_operation.get("requestBody", {})
            old_required = isinstance(old_body, dict) and old_body.get("required") is True
            new_required = isinstance(new_body, dict) and new_body.get("required") is True
            if new_required and not old_required:
                findings.append(
                    finding(
                        "REQUEST_BODY_BECAME_REQUIRED",
                        f"{location} requestBody",
                        "The request body became required.",
                    )
                )

            old_responses = old_operation.get("responses", {})
            new_responses = new_operation.get("responses", {})
            if isinstance(old_responses, dict) and isinstance(new_responses, dict):
                for status in old_responses:
                    if str(status).startswith("2") and status not in new_responses:
                        findings.append(
                            finding(
                                "SUCCESS_RESPONSE_REMOVED",
                                f"{location} response {status}",
                                "A documented successful response was removed.",
                            )
                        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path, help="baseline OpenAPI JSON or YAML")
    parser.add_argument("new", type=Path, help="candidate OpenAPI JSON or YAML")
    args = parser.parse_args()
    try:
        findings = detect_breaks(load_document(args.old), load_document(args.new))
        result: dict[str, Any] = {
            "status": "failed" if findings else "passed",
            "breaking_count": len(findings),
            "findings": findings,
            "limitations": [
                "Referenced and nested schemas are not semantically compared.",
                "Authentication and behavioral compatibility require human review.",
            ],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if findings else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

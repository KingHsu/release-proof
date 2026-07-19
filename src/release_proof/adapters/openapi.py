from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class OpenAPICompareError(ValueError):
    pass


def _load_spec(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise OpenAPICompareError("unable to parse OpenAPI document") from exc
    if not isinstance(payload, dict) or "openapi" not in payload:
        raise OpenAPICompareError("document is not an OpenAPI specification")
    return payload


def compare_openapi_files(base_path: Path, head_path: Path) -> dict[str, list[str]]:
    """Return conservative deterministic changes; it never claims full compatibility."""

    base = _load_spec(base_path)
    head = _load_spec(head_path)
    breaking: list[str] = []
    nonbreaking: list[str] = []
    base_paths = base.get("paths", {}) if isinstance(base.get("paths"), dict) else {}
    head_paths = head.get("paths", {}) if isinstance(head.get("paths"), dict) else {}
    for path, methods in base_paths.items():
        if path not in head_paths:
            breaking.append(f"removed path {path}")
            continue
        if not isinstance(methods, dict) or not isinstance(head_paths[path], dict):
            continue
        for method in methods:
            if method.lower() in {"parameters", "summary", "description"}:
                continue
            if method not in head_paths[path]:
                breaking.append(f"removed operation {method.upper()} {path}")
    for path, methods in head_paths.items():
        if path not in base_paths:
            nonbreaking.append(f"added path {path}")
            continue
        if isinstance(methods, dict) and isinstance(base_paths[path], dict):
            for method in methods:
                if method.lower() in {"parameters", "summary", "description"}:
                    continue
                if method not in base_paths[path]:
                    nonbreaking.append(f"added operation {method.upper()} {path}")
    return {"breaking": sorted(breaking), "nonbreaking": sorted(nonbreaking)}


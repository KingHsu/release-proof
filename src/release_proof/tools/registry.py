from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from release_proof.adapters.local_git import GitReadOnlyClient
from release_proof.adapters.openapi import compare_openapi_files
from release_proof.adapters.reports import ReportCollector


class GetChangeSummaryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_ref: str
    head_ref: str


class ListChangedFilesArgs(GetChangeSummaryArgs):
    pass


class ReadDiffArgs(GetChangeSummaryArgs):
    path: str


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=240, ge=1)


class SearchCodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=200)
    revision: str = "HEAD"


class ReadReportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class CompareOpenAPIArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_path: str
    head_path: str


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal[
        "get_change_summary",
        "list_changed_files",
        "read_diff",
        "read_file",
        "search_code",
        "read_test_report",
        "read_ci_summary",
        "compare_openapi",
        "list_migrations",
    ]
    arguments: dict[str, Any]


class ToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    call_key: str
    status: Literal["ok", "error"]
    output: Any = None
    error_category: str | None = None
    duration_ms: int


ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "get_change_summary": GetChangeSummaryArgs,
    "list_changed_files": ListChangedFilesArgs,
    "read_diff": ReadDiffArgs,
    "read_file": ReadFileArgs,
    "search_code": SearchCodeArgs,
    "read_test_report": ReadReportArgs,
    "read_ci_summary": ReadReportArgs,
    "compare_openapi": CompareOpenAPIArgs,
    "list_migrations": ListChangedFilesArgs,
}


class ReadOnlyToolRegistry:
    """Validated function-calling boundary; every operation is fixed and read-only."""

    def __init__(self, repository_path: str | Path, *, max_calls: int = 30) -> None:
        self.git = GitReadOnlyClient.for_repository(repository_path)
        self.reports = ReportCollector(self.git.policy)
        self.max_calls = max_calls
        self.calls: list[ToolObservation] = []
        self._seen_keys: set[str] = set()

    @staticmethod
    def schemas() -> dict[str, dict[str, Any]]:
        return {name: model.model_json_schema() for name, model in ARGUMENT_MODELS.items()}

    def execute(self, call: ToolCall) -> ToolObservation:
        if len(self.calls) >= self.max_calls:
            raise RuntimeError("tool call limit reached")
        arguments = ARGUMENT_MODELS[call.name].model_validate(call.arguments)
        canonical = json.dumps(
            {"name": call.name, "arguments": arguments.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        call_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        if call_key in self._seen_keys:
            raise RuntimeError("duplicate tool call rejected")
        self._seen_keys.add(call_key)
        started = time.perf_counter()
        try:
            output = self._dispatch(call.name, arguments)
            observation = ToolObservation(
                name=call.name,
                call_key=call_key,
                status="ok",
                output=output,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            observation = ToolObservation(
                name=call.name,
                call_key=call_key,
                status="error",
                error_category=type(exc).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        self.calls.append(observation)
        return observation

    def _dispatch(self, name: str, arguments: BaseModel) -> Any:
        values = arguments.model_dump()
        if name == "get_change_summary":
            return self.git.get_change_summary(**values).model_dump(mode="json")
        if name == "list_changed_files":
            return self.git.list_changed_files(**values)
        if name == "read_diff":
            return self.git.read_diff(
                values["base_ref"], values["head_ref"], values["path"]
            )
        if name == "read_file":
            return self.git.read_file(
                values["revision"],
                values["path"],
                values["start_line"],
                values["end_line"],
            )
        if name == "search_code":
            return self.git.search_code(**values)
        if name == "read_test_report":
            return [
                item.model_dump(mode="json")
                for item in self.reports.read(values["path"], evidence_prefix="tool-report")
            ]
        if name == "read_ci_summary":
            return [
                item.model_dump(mode="json")
                for item in self.reports.read_ci_snapshot(values["path"], evidence_prefix="tool-ci")
            ]
        if name == "compare_openapi":
            base = self.git.policy.validate_readable_file(values["base_path"])
            head = self.git.policy.validate_readable_file(values["head_path"])
            return compare_openapi_files(base, head)
        if name == "list_migrations":
            paths = self.git.list_changed_files(values["base_ref"], values["head_ref"])
            return [
                path
                for path in paths
                if "migration" in path.lower() or path.lower().endswith(".sql")
            ]
        raise RuntimeError("tool is not implemented")


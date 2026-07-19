from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from release_proof.adapters.github_mcp.client import GitHubMCPReadOnlyAdapter
from release_proof.adapters.github_mcp.transport import (
    MCPToolNotAllowedError,
    MCPTransportError,
    StdioMCPTransport,
)

PROJECT_ROOT = Path(__file__).parents[1]
FIXTURE_SERVER = PROJECT_ROOT / "tests" / "fixtures" / "mcp_readonly_server.py"


def fixture_transport(**overrides: object) -> StdioMCPTransport:
    options: dict[str, object] = {
        "command": sys.executable,
        "args": (str(FIXTURE_SERVER),),
        "cwd": PROJECT_ROOT,
        "allowed_tools": frozenset({"issue_read", "pull_request_read"}),
        "timeout_seconds": 8.0,
    }
    options.update(overrides)
    return StdioMCPTransport(**options)  # type: ignore[arg-type]


def test_stdio_transport_performs_real_initialize_and_tool_discovery() -> None:
    transport = fixture_transport()

    initialized = transport.initialize()
    assert initialized["protocolVersion"]
    assert initialized["serverInfo"]["name"] == "release-proof-readonly-fixture"

    tools = transport.list_tools()
    assert {tool["name"] for tool in tools} == {"issue_read", "pull_request_read"}
    issue = next(tool for tool in tools if tool["name"] == "issue_read")
    assert issue["annotations"]["readOnlyHint"] is True
    assert issue["annotations"]["destructiveHint"] is False


def test_stdio_transport_calls_tool_and_normalizes_into_evidence() -> None:
    transport = fixture_transport()
    adapter = GitHubMCPReadOnlyAdapter(transport=transport)

    evidence = adapter.fetch(
        "get_issue", {"owner": "acme", "repo": "payments", "issue_number": 17}
    )

    assert evidence.source_uri == "mcp+github://acme/payments/get_issue"
    assert evidence.metadata["upstream_tool"] == "issue_read"
    assert "Acceptance criteria" in evidence.content_excerpt
    assert "fixture-value-that-must-never-escape" not in evidence.content_excerpt
    assert "[REDACTED]" in evidence.content_excerpt


def test_stdio_transport_rejects_non_allowlisted_and_write_shaped_calls() -> None:
    transport = fixture_transport()

    with pytest.raises(MCPToolNotAllowedError, match="read-only allowlist"):
        transport.call_tool("issue_write", {"owner": "acme", "repo": "payments"})
    with pytest.raises(MCPToolNotAllowedError, match="write-shaped"):
        transport.call_tool(
            "issue_read",
            {"owner": "acme", "repo": "payments", "body": "please mutate"},
        )


def test_stdio_transport_error_does_not_expose_process_configuration(tmp_path: Path) -> None:
    secret = "never-print-this-token"
    transport = fixture_transport(
        command=str(tmp_path / f"missing-{secret}.exe"),
        args=(f"--token={secret}",),
        environment={"GITHUB_PERSONAL_ACCESS_TOKEN": secret},
        timeout_seconds=1.0,
    )

    with pytest.raises(MCPTransportError) as captured:
        transport.initialize()

    assert secret not in str(captured.value)
    assert secret not in repr(transport)

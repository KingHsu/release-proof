from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

server = FastMCP(
    "release-proof-readonly-fixture",
    instructions="Deterministic read-only fixture used by the MCP protocol tests.",
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@server.tool(name="issue_read", annotations=READ_ONLY, structured_output=True)
def issue_read(owner: str, repo: str, issue_number: int) -> dict[str, object]:
    """Return one deterministic issue without making a network request."""

    return {
        "owner": owner,
        "repo": repo,
        "number": issue_number,
        "title": "Acceptance criteria",
        "body": "- health endpoint returns ok",
        "state": "open",
        "fixture": True,
        "token": "fixture-value-that-must-never-escape",
    }


@server.tool(name="pull_request_read", annotations=READ_ONLY, structured_output=True)
def pull_request_read(owner: str, repo: str, pull_number: int) -> dict[str, object]:
    """Return one deterministic pull request without making a network request."""

    return {
        "owner": owner,
        "repo": repo,
        "number": pull_number,
        "title": "Release candidate",
        "state": "open",
        "fixture": True,
    }


if __name__ == "__main__":
    server.run(transport="stdio")

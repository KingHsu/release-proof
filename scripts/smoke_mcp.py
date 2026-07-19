from __future__ import annotations

import json
import sys
from pathlib import Path

from release_proof.adapters.github_mcp import GitHubMCPReadOnlyAdapter, StdioMCPTransport


def main() -> None:
    project_root = Path(__file__).parents[1]
    fixture_server = project_root / "tests" / "fixtures" / "mcp_readonly_server.py"
    transport = StdioMCPTransport(
        command=sys.executable,
        args=(str(fixture_server),),
        cwd=project_root,
        allowed_tools=frozenset({"issue_read", "pull_request_read"}),
        timeout_seconds=8.0,
    )

    initialized = transport.initialize()
    tools = transport.list_tools()
    evidence = GitHubMCPReadOnlyAdapter(transport).fetch(
        "get_issue",
        {"owner": "acme", "repo": "payments", "issue_number": 17},
    )

    # Intentionally print provenance only. The fixture payload is not emitted.
    print(
        json.dumps(
            {
                "protocol_handshake": "ok",
                "server": initialized["serverInfo"]["name"],
                "tools": sorted(tool["name"] for tool in tools),
                "called_operation": evidence.metadata["operation"],
                "evidence_id": evidence.id,
                "source_uri": evidence.source_uri,
                "observed_by": evidence.observed_by,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

# ReleaseProof

Evidence-grounded release acceptance — maps acceptance criteria to verifiable engineering evidence.

## What it does

ReleaseProof answers a narrower question than "does this code look good": **does each acceptance criterion have traceable implementation and verification evidence?**

It reads your Issue/requirements, Git diff, test reports, and CI snapshots, then produces an evidence matrix:

- `supported` — implementation + test/CI evidence found
- `partially_supported` — some evidence, gaps identified
- `unsupported` — no matching evidence
- `unable_to_determine` — insufficient input material

The final recommendation is capped at `ready_for_human_review` — the system **cannot** approve, merge, or deploy.

## Quickstart

**Requirements:** Python 3.11+, Git

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"
cp .env.example .env

# Offline demo
python scripts/create_demo_repo.py
release-proof analyze runtime/demo-repo \
  --base HEAD~1 --head HEAD \
  --requirement "- Health API returns an ok status" \
  --report reports/junit.xml

# API
release-proof serve --port 8002
# → http://localhost:8002/docs
```

## How it works

1. **Extract** — acceptance criteria from requirements (deterministic parser or LLM forced-schema tool)
2. **Collect** — read-only Git diff, JUnit XML, coverage JSON, CI snapshots
3. **Map** — evidence items to criteria via token overlap
4. **Validate** — every evidence link must be traceable to its source
5. **Gate** — deterministic policy sets the recommendation ceiling

Complex changes spanning multiple risk domains (API + migration + config) may use parallel specialist analysis; simple changes stay single-path. The default is always single-agent.

## API

| Method | Path | |
|---|---|---|
| `POST` | `/api/v1/analyses` | Create analysis |
| `GET` | `/api/v1/analyses/{id}` | Status and report |
| `GET` | `/api/v1/analyses/{id}/trace` | Node and tool trace |
| `POST` | `/api/v1/analyses/{id}/resume` | Supply missing reports or human input |
| `GET` | `/api/v1/skills` | Available review skills |
| `POST` | `/api/v1/evaluations` | Run benchmark |

## Quality

```bash
ruff check . && pyright && pytest --cov=release_proof
```

All tests run offline. CI uses fake LLM, local Git fixtures, and a fake MCP server.

## Tech stack

Python 3.11 · FastAPI · LangGraph · SQLite · Pydantic · DeepSeek API · MCP (Python SDK) · Streamlit · Docker

## Security

- All tools are read-only with Pydantic parameter validation and path allowlisting
- No execution of repository tests, no shell command generation, no write access
- Git commands use fixed parameter arrays (`shell=False`)
- `.env`, private keys, and credential files are blocked from reading
- PR/Issue/Diff/source code are treated as untrusted data, never system instructions
- Policy gate is deterministic code — the model's explanation can never override it

See [threat model](docs/threat-model.md) and [tool security](docs/tool-security.md).

## Limitations

- Single-user P0 — no RBAC, tenant isolation, or remote sandboxing
- Token-overlap evidence mapping is a transparent baseline, not a final semantic matcher
- OpenAPI comparator covers path/operation additions and removals, not full compatibility analysis
- Real GitHub MCP Server authentication and main-workflow integration are deferred to P1
- `ready_for_human_review` still requires human judgment

## Docs

- [Architecture](docs/architecture.md)
- [Agent state & recovery](docs/agent-state.md)
- [Tool security](docs/tool-security.md)
- [Threat model](docs/threat-model.md)
- [Evaluation](docs/evaluation.md)
- [ADR: single-agent by default](docs/adr/0001-single-agent-by-default.md)
- [ADR: read-only tool boundary](docs/adr/0002-read-only-tool-boundary.md)
- [ADR: MCP as adapter only](docs/adr/0003-mcp-adapter-only.md)

## License

MIT

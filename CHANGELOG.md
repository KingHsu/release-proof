# Changelog

## 0.1.0 - 2026-07-20

Initial engineering release.

### Evidence Collection
- Read-only local Git adapter: rev-parse, diff, diff-tree, show, grep with fixed parameter arrays.
- JUnit XML, Cobertura XML, JSON coverage, and CI snapshot parsers.
- OpenAPI breaking-change detector (path/operation additions and removals).
- Evidence ledger with SHA-256 content hashing and source provenance tracking.

### Agent Workflow (LangGraph)
- 9-node state graph: validate → collect → extract criteria → profile → load skills → route → build matrix → validate evidence → write report.
- SQLite checkpoint store with interrupt/resume using stable thread IDs.
- Idempotent interrupt node: all pre-interrupt DB writes use upsert semantics.
- Five stop conditions: step/tool/token/cost/no-progress limits.
- Offline deterministic fallback when LangGraph or DeepSeek are unavailable.
- Budget consumption tracked per run; shared `max_llm_calls` and `max_output_tokens` caps.

### Acceptance Analysis
- Dual extraction: deterministic Markdown checklist parser + DeepSeek forced-schema tool.
- Change profile with 7 risk domains detected by path and file-type heuristics.
- Single-agent path for simple changes; parallel specialist subgraphs gated by risk-domain count.
- Four domain specialists: API contract, data migration, test evidence, release runtime.
- Token-overlap evidence-to-criterion mapping (ASCII + CJK bigram baseline).

### Policy & Safety
- Deterministic policy gate: critical unsupported → NOT_READY; no verification evidence → max CONDITIONAL; failed validator → downgrade.
- Final recommendation ceiling: `ready_for_human_review` (never "approved").
- Authoritative language filter in JSON/Markdown reports.
- Write-shaped MCP action rejection; tool allowlist enforcement.

### Tools
- 9 read-only tools with Pydantic parameter schemas and policy enforcement.
- Path traversal prevention, extension allowlist (38 types), file size caps (1MB), secret pattern redaction.
- SHA-256 based tool-call deduplication in single-agent loop.
- All tool errors classified (tool_error, policy_error, timeout) and surfaced, never masked.

### Skills
- `api-compatibility-review`: OpenAPI diff rules, evidence requirements, standalone script.
- `database-migration-review`: migration order, reversibility, data compatibility checklist.
- `release-readiness-review`: cross-domain evidence aggregation, human-check templates.
- Each skill: SKILL.md with YAML frontmatter, references, scripts; loaded only when risk domain matches.

### MCP Integration
- GitHub MCP read-only anti-corruption adapter with 4 allowed read intents.
- Official MCP Python SDK stdio transport: initialize, tools/list, tools/call verified locally.
- Fake MCP transport for CI; readOnlyHint enforcement; sensitive field redaction.
- Real GitHub MCP Server authentication and main-workflow integration deferred to P1.

### Interfaces
- FastAPI with 7 endpoints: create analysis, get status/report, resume, trace, skills, evaluation.
- CLI with 6 subcommands: analyze, resume, get, doctor, eval, serve.
- Streamlit UI (Chinese): create analysis, acceptance matrix, risk report, evaluation comparison.

### Evaluation
- 8 offline change cases: simple-complete, missing-verification, implementation-omitted, migration-no-rollback, cross-domain, prompt-injection, failed-ci, async-idempotency.
- Three variants: direct LLM (PR text only), single (structured evidence), gated_multi (parallel specialists when qualified).
- 4 metrics: acceptance_coverage, unsupported_claim_rate, critical_risk_recall, route_accuracy.

### Infrastructure
- Docker with non-root user, read-only rootfs, no-new-privileges, dropped capabilities.
- GitHub Actions CI: ruff, pyright, pytest (fully offline), compileall syntax check.
- SQLite for runs and checkpoints; no PostgreSQL required.

### Documentation
- Architecture, agent state/interrupt contract, tool security model, threat model, evaluation guide.
- 3 ADRs: single-agent-by-default, read-only-tool-boundary, mcp-adapter-only.
- AI-assisted development disclosure.

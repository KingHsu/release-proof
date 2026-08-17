# Changelog

## Unreleased

- Add paired evidence-supported and unsupported walkthroughs, plus a preflight warning that
  blocks vague completion claims before an optional paid planner call.
- Add a zero-argument Chinese CLI guide and a Windows one-click launcher; the normal path asks
  only for the repository, requirement, and optional test report, and online use needs an
  explicit cost confirmation.
- Add an explicit one-request `probe-llm --confirm-paid-call` command with retries disabled and a 128-token output cap.
- Preserve safe structured-response diagnostics (stop reason and content-block types) without storing provider text, prompts, or credentials.
- Separate the provider's single structured-response tool from candidate read-action names, accept
  SDK object/dictionary blocks and JSON-string inputs, and retain safe validation categories plus
  provider usage on failed schema validation.
- Add a run-scoped model circuit breaker: after one structured-contract failure, remaining planner
  and specialist decisions use bounded deterministic fallbacks through the same tool harness.
- Normalize model-split test/report requirements into verification hints only when they share a
  concrete domain term with a behavioral criterion; explicit report deliverables stay independent.
- Preserve JUnit test names and repository-local report paths in locators, add fail-closed suite
  summaries for zero-failure claims, and match concrete verification hints separately from code.

## 0.2.0 - 2026-07-28

### Acceptance evidence
- Replace any-token evidence linking with explainable weighted matching, layer-specific evidence kinds, pass-state checks, confidence, matched terms, and signals.
- Preserve implementation and verification as separate requirements for a supported criterion.
- Require explicit passing test/CI metadata; missing or unknown verification status fails closed.
- Limit model-backed specialists to candidate evidence IDs and discard references that are absent from the hashed ledger.

### Bounded workflow
- Replace the fixed bulk collector in the main graph with a bounded, model-directed evidence loop using structured `NextAction` decisions.
- Add separate planner, tool admission, evidence ingest, and gap-recomputation nodes with conditional LangGraph edges.
- Persist action history, evidence IDs, duplicate keys, planner/tool counters, no-progress count, and an execution deadline across interrupt/resume while excluding measured human-pause time.
- Add an ordered `FakeStructuredLLM` planner demo plus rejection, duplicate, budget, and recovery tests without paid API calls.
- Route main-path Git and report reads through the typed `ReadOnlyToolRegistry`.
- Apply persistent run-state admission to real collection with shared action-key deduplication, call/step/time/no-progress limits, and traced tool observations.
- Accept a bounded external UTF-8 requirement file without requiring it to be committed, while hiding the absolute host path.
- Keep specialists conditional on deterministic multi-domain eligibility.

### Coding-agent Skills
- Turn `release-readiness-review` into an executable Codex/Claude Code entrypoint with a safe CLI wrapper and JSON/Markdown consistency checks.
- Add an original `java-microservice-release-review` Skill for cross-service contracts, configuration, batch idempotency, Oracle semantics, pagination, rollout, and rollback evidence.
- Detect Java/Spring/Oracle/batch/config changes and load the Java Skill only when relevant.
- Make the Java scanner read the requested Git revision rather than a dirty worktree in ref mode.

### Documentation and packaging
- Document ReleaseProof as a post-coding evidence gate rather than an autonomous coding agent.
- Publish exact Skill, MCP, specialist, and matcher boundaries.
- Remove the platform-specific frozen development snapshot; `pyproject.toml` is the dependency source of truth.

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
- Budget primitives for step/tool/token/cost/no-progress limits.
- Offline deterministic fallback when LangGraph or DeepSeek are unavailable.
- Budget consumption tracked per run; shared `max_llm_calls` and `max_output_tokens` caps.

### Acceptance Analysis
- Dual extraction: deterministic Markdown checklist parser + DeepSeek forced-schema tool.
- Change profile with 7 risk domains detected by path and file-type heuristics.
- Single-agent path for simple changes; parallel specialist subgraphs gated by risk-domain count.
- Four domain specialists: API contract, data migration, test evidence, release runtime.
- Token-overlap evidence-to-criterion mapping (ASCII + CJK bigram baseline, replaced in v0.2).

### Policy & Safety
- Deterministic policy gate: critical unsupported → NOT_READY; no verification evidence → max CONDITIONAL; failed validator → downgrade.
- Final recommendation ceiling: `ready_for_human_review` (never "approved").
- Authoritative language filter in JSON/Markdown reports.
- Write-shaped MCP action rejection; tool allowlist enforcement.

### Tools
- 9 read-only registry tools with Pydantic parameter schemas and policy enforcement; main-path registry wiring followed in v0.2.
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
- Three variants: direct completion claim (no evidence tools), single (structured evidence), gated_multi (route-gate comparison).
- 4 metrics: acceptance_coverage, unsupported_claim_rate, critical_risk_recall, route_accuracy.

### Infrastructure
- Docker with non-root user, read-only rootfs, no-new-privileges, dropped capabilities.
- GitHub Actions CI: ruff, pyright, pytest (fully offline), compileall syntax check.
- SQLite for runs and checkpoints; no PostgreSQL required.

### Documentation
- Architecture, agent state/interrupt contract, tool security model, threat model, evaluation guide.
- 3 ADRs: single-agent-by-default, read-only-tool-boundary, mcp-adapter-only.
- AI-assisted development disclosure.

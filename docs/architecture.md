# Architecture

ReleaseProof separates evidence rules from orchestration. Domain models, matching, validation, and policy remain ordinary Pydantic-backed Python; LangGraph coordinates state, interruption, and conditional specialists.

## Main path

1. Validate an explicit local repository root and Git refs.
2. Bootstrap only a frozen change summary and the explicit requirement source.
3. Extract independently verifiable acceptance criteria and profile risk domains.
4. Load relevant Skill instructions into the planner/specialist context.
5. Recompute implementation and verification gaps.
6. Ask the planner for one structured `NextAction`: `call_tool`, `request_input`, or `finish`.
7. Validate a proposed call in code, execute one fixed read-only adapter, and ingest only normalized `EvidenceItem` output.
8. Loop through gaps and actions until evidence is sufficient, no useful read remains, input is required, or a persistent limit stops the run.
9. Pause/resume with the same thread, evidence IDs, action keys, deadline, and counters.
10. Run conditional domain specialists only after evidence collection; they cannot raise the final recommendation.
11. Build the matrix, validate ledger hashes, apply the deterministic ceiling, and persist JSON/Markdown reports.

## Tool and execution boundary

`EvidenceToolHarness` is the only business-evidence execution boundary. The model sees five tools: `read_diff`, `read_file`, `search_code`, `read_test_report`, and `read_ci_summary`. Change-summary and requirement reads are deterministic bootstrap operations rather than model choices.

`ModelEvidencePlanner` uses the existing structured LLM boundary and `NextAction` schema. `FakeStructuredLLM` accepts ordered responses, so CI and the demo exercise a real multi-step model-planner call boundary without a paid API. Offline normal operation uses `DeterministicEvidencePlanner`, but still passes every action through the same harness.

The harness validates tool name, Pydantic arguments, criterion IDs, frozen refs, changed-file/report manifests, and repository path policy before dispatch. Persistent State enforces stable action-key deduplication, planner/tool steps, wall-clock deadline, and no-progress limits across interrupt/restart. A planner proposal is never evidence; only a successful, normalized tool observation can enter the ledger.

## Evidence matrix

Implementation and verification are separate layers. A criterion is `supported` only when both layers contain an accepted match.

The v0.2 matcher:

- removes generic vocabulary and normalizes small aliases;
- scores weighted criterion coverage, locator overlap, phrase match, and criterion-type/evidence-kind fit;
- enforces different allowed kinds for implementation and verification;
- rejects failed test or CI observations as verification;
- records match score, confidence, terms, and signals;
- accepts an explicit criterion ID only when trusted evidence metadata contains it.

This is an explainable retrieval baseline, not semantic entailment. Low-confidence or vocabulary-mismatched cases are kept as badcases instead of being silently promoted by model prose.

## Specialists and Skills

The coordinator derives risk domains from changed paths. Two or more independent domains are required for multi mode; tests and docs do not independently justify it.

Each selected domain uses the same structured specialist contract. Multi mode executes one-node LangGraph domain subgraphs through a bounded thread pool. A model-backed specialist receives only a candidate evidence pack and may return candidate evidence IDs. Unknown IDs are discarded, and only ledger-backed references reach a report.

Skill instructions are selected by deterministic profile rules. The Java Skill can inform a matching specialist context when Java/Spring/Oracle/batch/config signals exist. Its standalone scanner is invoked by a coding-agent Skill entrypoint, not automatically executed by the core graph.

## Persistence and recovery

- `release-proof.sqlite3`: run request, business snapshot, trace, interrupt, and final report.
- `checkpoints.sqlite3`: LangGraph state keyed by stable `thread_id`.

Full repositories and unbounded logs are not copied into checkpoints. Evidence excerpts are bounded and retain source locators.

The interrupted node is idempotent: pre-interrupt reads are repeatable, evidence IDs are stable inside the run, database snapshots use upsert semantics, and report files use the same run ID.

## Optional online adapters

DeepSeek structured extraction/specialists and the MCP transport are replaceable adapters. The real local MCP stdio path verifies initialize, discovery, call, annotations, and normalization, but the main analysis currently uses local Git/report providers. Online GitHub authentication and provider integration remain out of scope.

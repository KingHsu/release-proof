# Architecture

ReleaseProof uses a ports-and-adapters structure so that evidence rules remain testable without a model or agent framework.

## Data flow

1. Validate an explicit local repository root and two Git refs.
2. Run fixed read-only Git operations and parse pre-generated test/CI reports.
3. Normalize every observation into an immutable `EvidenceItem` with locator and hash.
4. Extract independently verifiable acceptance criteria.
5. Classify risk domains with deterministic path rules.
6. Pause when required evidence is missing, using a stable thread ID.
7. Activate only relevant Skills.
8. Use a single flow by default; fan out independent specialist subgraphs only for cross-domain changes.
9. Build the evidence matrix, validate every reference, and apply a deterministic policy ceiling.
10. Persist JSON and Markdown reports for human review.

## Why the domain does not import LangGraph

`domain/`, `evidence/`, the matrix builder, and policy gate accept Pydantic models and ordinary lists. This lets CI test the central safety claims without a network, checkpoint database, or LLM. `graph/` owns orchestration only.

## Persistence

- `release-proof.sqlite3`: business run snapshot, final report, trace, and interrupt payload.
- `checkpoints.sqlite3`: LangGraph checkpoint state by `thread_id`.

Large full files and raw logs are not copied into checkpoints. Evidence excerpts are bounded and point back to their source.

## Online extension

The DeepSeek and GitHub MCP adapters are optional ports. The current offline baseline keeps the product usable and testable while labeled evaluation data is collected. Any model-backed extractor must return the same domain schema and remains constrained by the evidence validator and policy gate.


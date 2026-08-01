---
name: java-microservice-release-review
description: Review Java microservice changes for cross-service contract propagation, enum and configuration gaps, batch idempotency, Oracle SQL semantics, multi-source pagination, and safe rollout or rollback. Use before a PR or release when changed Java, SQL, YAML, properties, XML, or deployment files can affect distributed behavior or production data.
---

# Java microservice release review

Perform a read-only, evidence-backed release-risk review. Treat static findings as prompts for verification, not proof that a change is unsafe or safe.

## Collect the change

1. Identify the repository and base/head refs.
2. Read the requirement and changed-file list.
3. Exclude generated files, vendored dependencies, and unrelated modules.
4. Preserve file/line evidence for every claim.

Run the bundled scanner:

```text
python scripts/scan_java_release_risks.py REPOSITORY --base BASE --head HEAD
```

Use repeated `--file RELATIVE_PATH` arguments when reviewing an explicit file set without Git refs. The script runs fixed read-only Git commands or reads allowlisted text files; it never executes code, tests, builds, migrations, or deployment commands.

## Investigate findings

Read only the relevant sections of:

- [risk patterns](references/risk-patterns.md) for the detected codes;
- [evidence checklist](references/evidence-checklist.md) when building the final release review.

For each finding:

1. Trace the affected value or contract from producer to consumer.
2. Distinguish implementation evidence from verification evidence.
3. State what the scanner observed and what remains unknown.
4. Request production-only facts such as table size, traffic, compatibility window, configuration ownership, or rollback timing from a human.
5. Mark a risk resolved only when linked evidence addresses it.

## Return the review

Return:

- changed files and refs reviewed;
- findings grouped by cross-service, data, configuration, batch, query, and rollout domains;
- evidence links and unresolved questions;
- required regression tests, observability, gray-release, and rollback checks;
- `ready_for_human_review`, `conditional`, or `not_ready`.

Never return autonomous approval. A clean static scan means only “no configured pattern matched.”

## Boundaries

- Do not use employer code, customer data, internal identifiers, credentials, or proprietary runbooks.
- Do not execute a repository, connect to a database, run SQL, mutate configuration, or deploy.
- Do not infer production safety from unit tests or a successful build alone.
- Do not expose source excerpts in the scanner output; use file, line, and rule identifiers.

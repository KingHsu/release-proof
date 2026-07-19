---
name: database-migration-review
description: Review PostgreSQL migration SQL for destructive operations, table-lock risks, unsafe data changes, and missing rollout evidence. Use before releasing a change that includes schema or data migrations.
license: Apache-2.0
compatibility: Requires Python 3.11. The bundled checker performs static analysis and never connects to a database.
metadata:
  author: release-proof
  version: "0.1.0"
---

# Database migration review

Use this skill when the candidate release contains SQL migrations or changes the persistence model. It is a release-risk review, not a SQL authoring agent.

## Required inputs

- Migration SQL in its actual execution order.
- Database engine and version; the bundled rules target PostgreSQL.
- Table size or traffic context for affected production tables.
- Roll-forward, compatibility-window, and rollback plans.

## Workflow

1. Run `python scripts/analyze_sql_migration.py MIGRATION.sql` for each migration.
2. Group findings by migration order and affected object.
3. Apply [the rollout checklist](references/rollout-checklist.md) to issues static SQL cannot settle.
4. Require explicit human review for every critical or high-risk finding.
5. Return evidence and unresolved questions; never execute the SQL.

## Hard boundaries

- Do not connect to a database, run migrations, mutate fixtures, or generate approval tokens.
- A clean static scan is not proof that a migration is safe at production scale.
- Rollback must account for application-version compatibility and irreversible data loss.
- Treat dialect-specific or dynamically generated SQL as `needs_human_review` when it cannot be parsed reliably.

## Output contract

Return the migration identifiers, ordered findings, production-context questions, rollback evidence, and a status of `passed`, `failed`, or `needs_human_review`.


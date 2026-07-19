# PostgreSQL migration rollout checklist

## Before release

- Estimate row counts, statement duration, lock level, and available maintenance window.
- Confirm old and new application versions can coexist during a rolling deployment.
- Prefer expand-and-contract changes over renaming or dropping in one release.
- Backfill in bounded batches and make the operation resumable and observable.
- Verify indexes used for constraints exist before enforcing the constraint.

## High-risk patterns

- `DROP`, `TRUNCATE`, destructive type conversions, and irreversible data rewrites.
- `SET NOT NULL` or validation on a large table without a staged plan.
- Non-concurrent index creation on a live, frequently written table.
- Unbounded `UPDATE` or `DELETE` statements.

## Evidence

- Link staging results and representative timing measurements.
- Link backup/restore evidence when loss is possible.
- State rollback and roll-forward triggers, owners, and expected duration.
- Record monitoring queries and the abort threshold.


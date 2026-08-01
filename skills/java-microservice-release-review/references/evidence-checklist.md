# Release review evidence checklist

## Change evidence

- Requirement or acceptance criterion with a stable identifier
- Base/head refs and changed-file list
- File/line locations for every finding
- Contract, mapping, SQL, configuration, and deployment changes in scope

## Verification evidence

- Producer/consumer contract tests for cross-service values
- Unknown and backward-compatibility cases for enums
- Missing, malformed, default, refresh, and rollback cases for configuration
- Duplicate delivery, retry, blank-key, and partial-failure cases for batch writes
- Null fixtures and representative execution plans for Oracle query changes
- Page-boundary and stable-order cases for multi-source pagination

## Operational evidence

- Metrics and logs that reveal silent drops, duplicates, fallback use, and partial failure
- Gray-release switch, owner, observation window, and abort threshold
- Roll-forward and rollback order across application, schema, configuration, and message contracts
- Explicit human confirmation for data volume, traffic, lock duration, and irreversible changes

## Recommendation rules

- Use `not_ready` when a critical acceptance condition is unsupported or a high-risk change lacks a safe rollout/rollback path.
- Use `conditional` when implementation exists but verification or production context is missing.
- Use `ready_for_human_review` only when evidence is traceable and all remaining decisions are explicitly assigned to humans.
- Never use a static scanner result as approval.

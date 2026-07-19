# ADR 0002: Keep review tools read-only

- Status: Accepted
- Date: 2026-07-20

## Context

The system processes untrusted repository content and model-generated plans. Allowing it to edit code, approve pull requests, run migrations, or deploy would turn an evidence assistant into an autonomous release authority and increase prompt-injection and operational risk.

## Decision

All P0 tools may only read repository, contract, CI, test, and migration artifacts. They produce structured findings with provenance. Merge, approval, migration, deployment, exception acceptance, and credential-bearing writes remain human-controlled and outside the tool interface.

## Consequences

The product can support decisions without becoming a privileged control plane. Some workflows require a manual handoff, which is intentional. Any future write capability requires a separate ADR, least-privilege credentials, explicit confirmation, audit logging, and a reversible operation design.


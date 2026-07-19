# ADR 0001: Default to a single agent

- Status: Accepted
- Date: 2026-07-20

## Context

Most release reviews are small and sequential. Starting several agents increases latency, token cost, duplicated context, and disagreement without guaranteeing better evidence.

## Decision

Use one stateful review agent by default. Route to independent API, database, test, or rollout analyzers only when a deterministic complexity gate detects multiple affected risk domains that can be inspected in parallel. Keep a single-agent fallback and measure completeness, latency, and cost before enabling multi-agent routing by default for any change class.

## Consequences

Simple changes stay understandable and inexpensive. Cross-domain changes can still gain parallel specialist analysis. The router and merge step need regression tests, and multi-agent use must be justified by evaluation data rather than feature presence.


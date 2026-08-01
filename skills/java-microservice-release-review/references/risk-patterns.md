# Java microservice release risk patterns

Load only the sections that match scanner findings or changed-file evidence.

## Cross-service field propagation

Trace every added, removed, renamed, or retyped field across request/response DTOs, serializers, RPC or HTTP contracts, adapters, persistence models, mapping code, and downstream consumers. Check mixed-version compatibility during rolling deployment. Require a consumer-facing contract test when a value crosses a service boundary.

## Enum and mapping coverage

Check database values, wire values, switch expressions, mapper defaults, serialization names, and unknown-value behavior. An enum declaration change is not proof that every producer and consumer supports the value. Prefer explicit unknown handling over silent nulls.

## Configuration completeness

Identify the owner, default, environment overrides, refresh behavior, validation, and rollback value for each new key. Verify that missing or malformed configuration fails visibly. For dynamic configuration, distinguish “can refresh” from “safe to change while requests are in flight.”

## Batch and asynchronous idempotency

Define the stable business key before accepting a scheduled, message-driven, or batch write. Inspect null and blank-key behavior, retry semantics, uniqueness constraints, upsert rules, partial failure, and duplicate delivery. Require replay tests and duplicate metrics for high-impact paths.

## Oracle NULL and `NOT IN`

`NULL NOT IN (...)` evaluates to unknown, which can silently remove rows. Inspect nullable left operands and nulls returned by the subquery. Prefer an explicitly justified `IS NULL OR ...`, `NOT EXISTS`, or other semantics backed by fixtures containing nulls.

## Oracle date and index behavior

Flag conversions such as `TO_DATE(SYSDATE)` that force unnecessary implicit conversion or obscure index use. Compare like types, use appropriate date boundaries, and confirm execution plans on representative data before production claims.

## Multi-source pagination

Independent pagination followed by in-memory concatenation does not produce global pagination. Define a global sort key and total-count semantics. Prefer a database-level union/global window when sources are query-compatible; otherwise use an explicit merge algorithm with deterministic cursors and regression cases across page boundaries.

## Transaction and remote-call boundaries

A local transaction does not atomically cover RPC, HTTP, messages, or another database. Check timeout, retry, duplicate effects, compensation, and observable partial states. Avoid claiming atomicity without an actual protocol that provides it.

## Gray release and rollback

Specify compatibility order, feature/config switch, observation window, abort thresholds, rollback owner, and data rollback limits. Schema and message changes often require expand-and-contract rather than application-only rollback.

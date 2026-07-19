# Evaluation

## Question

Does collecting and validating evidence reduce unsupported completion claims, and does conditional multi-agent routing identify cross-domain risk without wasting the simple path?

## Variants

- `direct`: PR completion claim only; no tools or risk profiler.
- `single`: deterministic evidence matrix and single route.
- `gated_multi`: same evidence logic, with deterministic cross-domain routing.

## Metrics

- `acceptance_coverage`: fraction of criteria receiving a determinate status;
- `unsupported_claim_rate`: expected unsupported criteria incorrectly labeled supported, divided by supported claims;
- `critical_risk_recall`: expected critical domains found by the change profiler;
- `route_accuracy`: single/multi decision against fixture expectation.

## Fixture provenance

The eight initial cases are controlled mutations, not production data. They exercise implementation omission, missing verification, migration recovery, cross-domain routing, prompt injection, failed CI, and async idempotency. They contain no employer or customer code.

## What the current evaluation cannot claim

- It does not measure DeepSeek accuracy, latency, or cost.
- It does not show production recommendation accuracy.
- Token-overlap evidence mapping is a transparent baseline, not a final semantic matcher.
- Multi-agent output quality is not proven merely because routing is correct.

Before publishing model metrics, add independently labeled real-project or public snapshots, keep holdout cases, version prompts/model/pricing, and report confidence intervals or raw counts alongside averages.


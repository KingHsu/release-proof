# Evaluation

## Questions

The controlled suite asks:

1. Does requiring implementation **and** verification evidence reduce unsupported completion claims?
2. Does deterministic routing keep simple changes on one path and identify changes with independent risk domains?
3. Does a stricter matcher avoid linking a criterion to evidence through one generic shared word?

## Variants

- `direct`: trusts the fixture PR completion claim and uses no evidence tools.
- `single`: builds the deterministic evidence matrix and forces the single route.
- `gated_multi`: uses the same matrix and enables the deterministic route gate.

`direct` is a completion-claim baseline, not an LLM benchmark. `gated_multi` measures route choice in this suite; it does not prove that parallel specialists improve analysis quality.

## Metrics

- `acceptance_coverage`: criteria receiving a determinate status;
- `unsupported_claim_rate`: expected-unsupported criteria incorrectly marked supported, divided by supported claims;
- `critical_risk_recall`: expected critical domains found by the deterministic profiler;
- `route_accuracy`: selected single/multi route versus fixture expectation.

Read raw counts as well as rates. A zero denominator is handled explicitly by the runner and should never be advertised as production accuracy.

## Fixture provenance

The eight JSON cases are original controlled mutations covering:

- complete simple change;
- missing verification;
- implementation omission;
- migration without rollback;
- cross-domain change;
- prompt-injection text;
- failed CI;
- async idempotency.

They contain no employer or customer code. Their purpose is deterministic regression and failure analysis.

## Limits

- No production recommendation accuracy is measured.
- No DeepSeek latency, token, cost, or answer-quality result is implied.
- Route accuracy is not multi-agent quality.
- The explainable matcher is lexical/metadata based and can miss semantic paraphrases.
- A public case study on a real repository change is still a single project example, not an external benchmark.

Before publishing model metrics, add independently labeled public-project snapshots, preserve a holdout set, version prompt/model/pricing, and report confidence intervals or raw counts.

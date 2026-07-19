# Agent state, interrupt, and recovery

The durable graph runs these nodes:

```text
validate_request
  -> collect_change_facts
  -> extract_acceptance_criteria
  -> profile_change
  -> request_missing_context (dynamic interrupt)
  -> refresh_after_resume
  -> load_relevant_skills
  -> route_analysis
  -> build_acceptance_matrix
  -> policy_gate_and_report
```

## Interrupt contract

An interrupt returns only JSON-serializable values:

- `run_id`;
- concrete reasons;
- concrete requested inputs.

Resume accepts report paths inside the configured repository, a CI snapshot, bounded clarifications, or an explicit request to continue with incomplete evidence.

## Idempotency

LangGraph restarts the interrupted node from its beginning. Code before `interrupt()` therefore performs no external writes. Git/report reads are repeatable, evidence IDs are stable within a run, and the business run store uses an upsert. Report files are overwritten by the same run ID.

## Fallback

If LangGraph or its SQLite checkpointer is unavailable, the service reports `offline-fallback` in `/health`. The same nodes run synchronously and business state is stored in SQLite. This is a development fallback, not a claim that framework persistence was exercised.

## Stop conditions

`ExecutionBudget` stops on:

- step limit;
- tool-call limit;
- repeated identical action key;
- configured no-progress count;
- elapsed-time limit.

The current P0 collector is a bounded pass rather than an open-ended model loop. A stopped run can still emit a partial report, but the policy gate prevents it from becoming `ready_for_human_review`.


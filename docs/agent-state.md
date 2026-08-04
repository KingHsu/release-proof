# Agent state, interrupt, and recovery

## Durable graph

```text
validate_request
  -> bootstrap_change_facts
  -> extract_acceptance_criteria
  -> profile_change
  -> load_relevant_skills
  -> compute_evidence_gaps
  -> choose_next_action
       ├─ call_tool -> validate_and_execute_readonly_tool
       │                -> ingest_evidence
       │                -> compute_evidence_gaps ─┐
       │                                          └─ loop
       ├─ request_input -> interrupt / resume -> compute_evidence_gaps
       └─ finish
  -> route_analysis
       ↳ single specialists, or bounded parallel domain subgraphs
  -> build_acceptance_matrix
  -> write_report
```

The business read loop is model-directed when an online LLM is configured and deterministic in offline mode. Both planners emit the same structured action and have no direct executor access. A domain “agent” remains a conditional specialist subgraph; the project does not claim autonomous negotiation.

## Interrupt contract

An interrupt contains JSON-serializable:

- `run_id`;
- concrete reasons;
- concrete requested inputs.

Resume accepts report paths inside the analyzed repository, a CI snapshot, bounded clarification, or an explicit instruction to continue with incomplete evidence. Continuing cannot bypass the policy penalty for missing verification.

## Idempotency

LangGraph restarts an interrupted node from the beginning. Code before `interrupt()` performs no external mutation. The checkpoint and run store retain evidence, `seen_action_keys`, `agent_steps_used`, `tool_count`, `no_progress_count`, action history, and an absolute `deadline_at`. Resume adds declared reports/CI/clarification and returns to gap computation; it does not bulk recollect or reset a counter.

## Budget and stop reasons

The planner/harness loop records:

- admitted unique tool calls;
- progress steps;
- consecutive no-progress observations;
- elapsed time;
- tool call key, status, error category, and duration.

Possible stop reasons include `step_limit`, `tool_call_limit`, `llm_call_limit`, `duplicate_tool_action`, `no_progress_limit`, `elapsed_time_limit`, `tool_policy_rejected`, and `planner_error`. A partial run can still produce a report, but the deterministic gate prevents a budget-exhausted analysis from becoming `ready_for_human_review`.

LLM call/output limits are separate from deterministic read-tool limits and are also persisted in trace usage. A structured provider failure sets `llm_degraded` for that run: no later planner or specialist call uses the provider, deterministic planning continues one bounded action at a time, and missing provider usage is counted explicitly instead of being reported as zero cost.

## Fallback

If the LangGraph SQLite checkpointer cannot initialize, the same node contract can run synchronously and business state remains in SQLite. Health output labels this as an offline fallback; it is not reported as checkpoint recovery.

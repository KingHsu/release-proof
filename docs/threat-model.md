# Threat model

## Protected assets

- local credentials and environment variables;
- files outside the selected repository;
- repository integrity;
- API/GitHub budget and permissions;
- correctness of evidence links and release recommendation ceiling.

## Untrusted inputs

Issue and PR text, Diff content, source code, report text, MCP payloads, path parameters, and LLM output are all data. None can override system policy.

## Main threats and controls

| Threat | Control | Residual risk |
|---|---|---|
| Prompt injection in PR/Diff | Prompt boundary, no free-form commands, deterministic policy | Model explanation can still be misleading; evidence links remain mandatory |
| Path traversal/symlink escape | Resolve-under-root checks and blocked names | Windows junction edge cases need broader platform testing |
| Secret exposure | File denylist, output redaction, no env dump | Secrets embedded in ordinary source may appear in bounded Diff |
| Arbitrary code execution | No repository test execution, fixed Git commands | Git parsers still process attacker-controlled repository data |
| MCP privilege escalation | Four internal read intents, read-only server configuration | Upstream tool names and schemas may change |
| AI self-endorsement | Code and test evidence separated; deterministic gate | Evidence can be incomplete or test labels can be poor |
| Checkpoint duplication | Idempotent interrupt node and upserted run store | Multi-process writer coordination is not production hardened |
| Denial of service | file/output/count/time limits | Huge repositories can still make Git operations expensive |

## Out of scope for P0

RBAC, tenant isolation, containerized repository sandboxing, webhook verification, enterprise audit retention, SAST, dependency scanning, and production incident response.


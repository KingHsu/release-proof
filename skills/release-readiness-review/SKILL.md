---
name: release-readiness-review
description: Verify a code change against acceptance criteria using traceable Git, test, CI, and rollback evidence. Use from Codex, Claude Code, or another coding agent after implementation, before opening or approving a PR, preparing a release, or claiming that work is complete.
---

# Release readiness review

Treat the coding agent's completion claim as untrusted. Run ReleaseProof, inspect its JSON and Markdown reports, and return an evidence-backed recommendation for human review.

## Gather inputs

- Identify the repository, base ref, head ref, and explicit acceptance criteria.
- Reuse a requirement file when one exists. Do not infer missing acceptance criteria from the implementation.
- Add existing JUnit, coverage, or CI snapshot paths. Do not run arbitrary repository commands to manufacture evidence.
- Ask for missing inputs when the repository or refs are ambiguous.

## Run the review

Resolve the wrapper relative to the directory containing this `SKILL.md`, then invoke that absolute path with a subprocess argument array. Do not assume the current directory is the Skill directory:

```text
python ABSOLUTE_SKILL_DIR/scripts/run_release_review.py REPOSITORY
  --base BASE
  --head HEAD
  --requirement-file REQUIREMENTS.md
  --report reports/junit.xml
  --output-dir STAGING/release-proof-review
```

Use `--requirement "..." --staging-dir STAGING` only for short inline criteria. The wrapper creates a temporary requirement file, calls `release-proof analyze --requirement-file`, consumes the generated JSON and Markdown reports, and removes its temporary file.

Then:

1. Read the wrapper's JSON summary from stdout.
2. Open `report_json` for machine-readable evidence and `report_markdown` for the human-facing report.
3. If `status` is `awaiting_input`, request the exact missing evidence and resume through ReleaseProof rather than guessing.
4. Report unsupported and partially supported criteria before the overall recommendation.
5. Keep `ready_for_human_review` non-binding.
6. Keep the output directory as review evidence until the user or repository owner decides its retention; it contains reports and the local run database. The wrapper cleans only its own temporary requirement file.

Generated reports are UTF-8. When manually inspecting them with legacy Windows PowerShell, specify UTF-8 explicitly.

Read [report interpretation](references/report-interpretation.md) when translating statuses or handling an interrupted/failed run. Use [the evidence manifest format](references/evidence-manifest.md) only when validating a standalone evidence manifest with `check_release_evidence.py`.

## Hard boundaries

- Do not merge code, approve a pull request, deploy, modify CI, or suppress a failed check.
- LLM reasoning is not itself implementation evidence.
- A tool execution error is an unknown result, not a passing result.
- Manual exceptions require a named owner, reason, and expiration or follow-up condition.
- Never use `shell=True`, concatenate a command string, or pass a model-generated executable.
- Do not delete an existing requirement file; the wrapper removes only a temporary file it created.

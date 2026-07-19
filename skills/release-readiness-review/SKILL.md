---
name: release-readiness-review
description: Verify that acceptance criteria and required release checks have concrete evidence. Use after implementation and CI complete, before a human makes the release decision.
license: Apache-2.0
compatibility: Requires Python 3.11 and a release-evidence JSON manifest matching the bundled reference schema.
metadata:
  author: release-proof
  version: "0.1.0"
---

# Release readiness review

Use this skill at the release gate, after requirements, code changes, and CI evidence are available. It checks evidence completeness; it does not decide business acceptance or perform a deployment.

## Required inputs

- Acceptance criteria with stable identifiers.
- Concrete evidence for each criterion: tests, code locations, screenshots, reports, or approved manual checks.
- Required release checks and their observed results.
- Open risks, exceptions, rollback plan, and named owners.

## Workflow

1. Build a manifest using [the evidence manifest format](references/evidence-manifest.md).
2. Run `python scripts/check_release_evidence.py MANIFEST.json`.
3. Resolve inconsistent claims such as `verified` without evidence.
4. Review exceptions and risks with their human owners.
5. Produce a recommendation of `ready_for_human_review` or `not_ready`; never produce an autonomous approval.

## Hard boundaries

- Do not merge code, approve a pull request, deploy, modify CI, or suppress a failed check.
- LLM reasoning is not itself implementation evidence.
- A tool execution error is an unknown result, not a passing result.
- Manual exceptions require a named owner, reason, and expiration or follow-up condition.

## Output contract

Return the status of every acceptance criterion and required check, evidence links, blocking gaps, known risks, and a final non-binding recommendation.

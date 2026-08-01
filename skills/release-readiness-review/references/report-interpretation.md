# Report interpretation

## Run states

- `completed`: Inspect both reports. A successful process is not the same as a ready release.
- `awaiting_input`: Report the interrupt reasons and requested inputs. Do not replace missing evidence with inference.
- `failed`: Report the sanitized errors and stop. Do not reuse a stale report from another run.

## Criterion states

- `supported`: Implementation and verification evidence are linked.
- `partially_supported`: Some evidence exists, but a stated gap remains.
- `unsupported`: No sufficient evidence supports the criterion.
- `unable_to_determine`: Inputs are insufficient or ambiguous.
- `not_applicable`: Require an explicit reason and supporting context.

## Recommendation ceiling

`ready_for_human_review` means only that the evidence package can enter human review. Every unresolved human check attached to `conditional`, `not_ready`, or `insufficient_evidence` is blocking until a named human resolves it or records an explicit exception. Never translate any state into autonomous approval, merge, or deployment authorization.

## Evidence handling

Prefer stable evidence identifiers, file/line locators, test case names, CI run identifiers, and content hashes. A PR description, coding-agent summary, or model explanation is a claim, not implementation or verification evidence.

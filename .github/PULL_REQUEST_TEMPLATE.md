## Acceptance criteria

List the independently verifiable conditions this change is expected to satisfy.

- [ ] Criterion 1:

## Implementation and verification evidence

- [ ] Link each criterion to an implementation diff.
- [ ] Link each criterion to a test, CI check, or bounded human check.
- [ ] Include migration, compatibility, rollout, and rollback evidence when relevant.

## Agent and tool boundaries

- [ ] New tools are narrowly scoped and read-only unless the change explicitly says otherwise.
- [ ] Untrusted repository content cannot override system or Skill instructions.
- [ ] Model output cannot bypass evidence validation or the deterministic policy gate.
- [ ] Missing evidence causes a pause, downgrade, or refusal instead of a guessed approval.

## Evaluation

- [ ] Added a controlled regression case or a public-repository case study.
- [ ] Reported quality, unsupported claims, latency, and cost when they are affected.
- [ ] Described why a single path or conditional specialist route is appropriate.

## AI-assisted changes

If an AI coding tool contributed, identify the generated parts and the evidence used
to review them. An Agent's completion claim is not acceptance evidence.

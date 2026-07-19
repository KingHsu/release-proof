# API compatibility checklist

Use the checker for mechanical differences, then review these questions manually.

## Contract surface

- Was a path or HTTP operation removed or renamed?
- Did an existing parameter become required?
- Did the request body become required?
- Was an existing successful response removed?
- Were media types, authentication requirements, pagination, or rate limits changed?

## Schema semantics

- Were enum values removed or validation bounds narrowed?
- Did nullable fields become non-nullable?
- Did a response field change type, meaning, units, or default behavior?
- Can old clients still omit newly introduced fields?

## Evidence

- Link the baseline and candidate specifications.
- Link consumer contract tests or compatibility tests.
- Identify affected clients and the migration or deprecation window.
- Record an owner for every accepted exception.


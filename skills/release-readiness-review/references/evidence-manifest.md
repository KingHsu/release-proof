# Release evidence manifest

The checker accepts a JSON object with this shape:

```json
{
  "release_id": "2026.07.20-rc1",
  "acceptance_criteria": [
    {
      "id": "AC-1",
      "description": "The API rejects an invalid token",
      "status": "verified",
      "evidence": [
        {"type": "test", "ref": "tests/test_auth.py::test_invalid_token"}
      ]
    }
  ],
  "required_checks": ["tests", "rollback_plan"],
  "checks": {
    "tests": {"status": "passed", "ref": "ci/run/123"},
    "rollback_plan": {"status": "present", "ref": "docs/runbook.md"}
  },
  "risks": [
    {"severity": "medium", "description": "Cache warms after deploy", "owner": "team-a"}
  ]
}
```

Criterion statuses are `verified`, `partial`, `missing`, or `not_applicable`. A verified criterion must have at least one evidence object with a non-empty `ref`.

Required check statuses that count as complete are `passed`, `present`, `verified`, and `not_applicable`. Use `not_applicable` only with explicit evidence explaining why.

Critical and high risks block readiness. Risk acceptance remains a human decision.


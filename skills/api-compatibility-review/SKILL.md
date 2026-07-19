---
name: api-compatibility-review
description: Review an OpenAPI contract change for client-breaking removals and newly required inputs. Use when a release changes an HTTP API and compatibility evidence is needed before approval.
license: Apache-2.0
compatibility: Requires Python 3.11. The bundled checker accepts JSON directly and YAML when PyYAML is installed.
metadata:
  author: release-proof
  version: "0.1.0"
---

# API compatibility review

Use this skill only when a change contains an OpenAPI contract or clearly changes a public HTTP API. Do not invoke it for internal refactors with no contract change.

## Required inputs

- The baseline OpenAPI document from the currently deployed version.
- The candidate OpenAPI document from the proposed release.
- The intended compatibility policy, if it is stricter than this skill's baseline rules.

## Workflow

1. Run `python scripts/detect_openapi_breaks.py OLD NEW`.
2. Read every structured finding; do not infer compatibility from the summary count alone.
3. Use [the review checklist](references/review-checklist.md) for semantics the script cannot prove.
4. Link each reported risk to the affected path, method, parameter, request body, or response.
5. Return `passed`, `failed`, or `needs_human_review` with evidence. Never report `passed` when the documents could not be parsed.

## Hard boundaries

- The script is a deterministic first pass, not a full OpenAPI compatibility engine.
- Do not edit either contract, generate a patch, deploy an API, or waive a breaking change.
- Do not treat a versioned replacement endpoint as proof that existing clients are safe.
- Authentication, authorization, schema semantics, enum narrowing, and behavioral changes require human review unless separately evidenced.

## Output contract

Return:

- the two contract identifiers;
- a list of breaking findings with locations;
- unresolved semantic questions;
- the final status and the evidence used for it.


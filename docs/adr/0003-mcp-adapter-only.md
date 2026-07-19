# ADR 0003: Treat MCP as an adapter

- Status: Accepted
- Date: 2026-07-20

## Context

Local repositories and fixture files do not need a protocol layer. Remote GitHub or CI systems may expose data through MCP, but binding domain logic to MCP would complicate tests and make the orchestration dependent on one transport.

## Decision

Define internal read-only ports for issues, pull requests, diffs, checks, and artifacts. Implement local and direct API adapters first. Add MCP adapters only for external systems where MCP provides useful interoperability. MCP responses are normalized into the same internal evidence models and never control routing or approval policy directly.

## Consequences

Core review logic remains transport-independent and works offline with fixtures. MCP integration is replaceable and independently testable. The adapter must preserve provenance, surface tool errors as unknown results, and enforce the same read-only boundary as every other integration.


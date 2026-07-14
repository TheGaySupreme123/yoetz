# tests/unit/service/test_client.py — ordinary ServiceClient unit suite

**Wave:** C/D | **ADRs:** ADR-008 | **Imports (spec-tree):** `src/yoetz_core/service/client.md` | **Imported by:** test runner

## Purpose

Verify typed request/result conversion, reconnect/cancel behavior, and client-kind authority.

## Public surface

Fake-control-stream tests for all ServiceClient methods and close/fork states.

## Behavior

Assert request IDs survive, RPC IDs differ, results validate, MCP lifecycle methods fail, and response loss never implies outcome.

## Errors and edge cases

Absent/locked/draining/wrong-peer/stale-generation/partial-response and replay paths.

## Invariants

1. No direct runtime/application fallback constructor exists.
2. Client types contain no secret handle.

## Tests

This file is the executable owner.

## Open questions

None.

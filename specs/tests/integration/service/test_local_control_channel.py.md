# tests/integration/service/test_local_control_channel.py — authenticated control transport integration

**Wave:** C | **ADRs:** ADR-008 | **Imports (spec-tree):** control port/protocol/Unix-socket specs | **Imported by:** test runner

## Purpose

Verify real same-UID three-endpoint isolation, ordinary framing/concurrency/cancellation, and
client-kind dispatch.

## Public surface

Async listener/client matrix for CLI, MCP, UI and all service states/methods.

## Behavior

Exercise partial IO, backpressure, out-of-order results, stale generation, response loss, three
fixed endpoint attacks/cross-protocol frames, and frozen schemas.

## Errors and edge cases

Wrong UID/mode/type, stale socket, malformed/oversize frame, 33rd concurrent request, disconnect.

## Invariants

1. Peer auth precedes parse.
2. Ordinary channel cannot carry confidential ingress.
3. Ordinary, YZS1, and YZH1 listeners reject each other's magic and client imports.

## Tests

This file is the executable owner.

## Open questions

None.

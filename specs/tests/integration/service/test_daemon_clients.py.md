# tests/integration/service/test_daemon_clients.py — multi-surface one-service integration

**Wave:** C/D | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** daemon/client/application specs | **Imported by:** test runner

## Purpose

Prove concurrent CLI/MCP/UI clients share one ready application/runtime and identical results.

## Public surface

In-process daemon with memory/storage fakes and concurrent typed clients for every method.

## Behavior

Count one application/provider/runtime construction, route workflows/support, disconnect/reconnect,
relock/reunlock fresh composition. Build the private production root against owner-only temporary
paths and fake authenticated listener binders; prove it acquires singleton authority before bind,
starts locked without creating ready-only vault/catalog state, and closes in reverse order.

## Errors and edge cases

Client crash, response loss, locked/draining, one misbehaving client, provider unavailable.
Block a ready factory mid-construction while a concurrent explicit lock starts; lock must wait,
observe the published ready generation, drain it, close the application once, and leave lifecycle
locked with the vault not ready. Revalidation failure after factory return closes the partial
application and reports only `unlock_failed`.

## Invariants

1. Client lifetime never owns service resources.
2. Same request/result is surface-parity exact.
3. Canonical installation marker round-trip preserves owner-only mode and validates its self-digest.

## Tests

This file is the executable owner.

## Open questions

None.

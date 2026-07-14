# tests/integration/service/test_daemon_clients.py — multi-surface one-service integration

**Wave:** C/D | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** daemon/client/application specs | **Imported by:** test runner

## Purpose

Prove concurrent CLI/MCP/UI clients share one ready application/runtime and identical results.

## Public surface

In-process daemon with memory/storage fakes and concurrent typed clients for every method.

## Behavior

Count one application/provider/runtime construction, route workflows/support, disconnect/reconnect, relock/reunlock fresh composition.

## Errors and edge cases

Client crash, response loss, locked/draining, one misbehaving client, provider unavailable.

## Invariants

1. Client lifetime never owns service resources.
2. Same request/result is surface-parity exact.

## Tests

This file is the executable owner.

## Open questions

None.

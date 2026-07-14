# tests/integration/service/test_multi_client_single_writer.py — shared service writer integration

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** runtime/daemon/client/SQLite specs | **Imported by:** test runner

## Purpose

Prove all clients coalesce on one catalog/task writer and current generation.

## Public surface

Concurrent multi-client workflows over one and multiple tasks with writer instrumentation.

## Behavior

Assert one writer/key context per task, bounded queue/cache, least-authority reads, fair dispatch, relock/restart generation advance.

## Errors and edge cases

Cold-open race, saturation, disconnect, stale handle, crash during commit.

## Invariants

1. No client opens storage.
2. At most one service writer per DB.

## Tests

This file is the executable owner.

## Open questions

None.

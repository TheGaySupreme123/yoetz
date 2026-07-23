# tests/unit/application/test_observation_coordinator.py

**Wave:** D | **Owns:** focused unit coverage for observation ingest request codecs,
materialization mapping, local outbox, and coordinator rejection without mapping.

## Purpose

Prove coordinator normalization, durable local fences, and idempotent routing behavior.

## Public surface

Pytest unit cases.

## Behavior

- `ObservationIngestRequest` JSON round-trip excludes task/writer IDs
- Pre/post/unpaired materialization shapes
- Local outbox enqueue/ack/overflow, quarantine aggregate, and generation-scoped stop
- SQLite ingest idempotent duplicate
- Coordinator rejects `mapping_missing` without calling runtime route
- Control handlers route `ObservationIngestRequest` via `ingest_request`

## Errors and edge cases

Rebooted monotonic epochs, stale session ends, duplicate ingest, and permanent rejection remain
explicit.

## Invariants

No test writes raw task content into local structural state.

## Tests

This file.

## Open questions

None.

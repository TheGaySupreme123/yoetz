# tests/unit/application/test_observation_health.py

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `application/observation_health.md`

## Purpose

Prove ACTIVE/DEGRADED/STALE/STOPPED lifecycle rules with an injected monotonic clock.

## Public surface

Pytest unit cases over pure lifecycle computation.

## Behavior

- Active when mapped, draining, and fresh
- Stale when progress exceeds threshold
- Single historical event does not stay active
- Stopped without consent
- Degraded with pending outbox or material gap

## Errors and edge cases

Old-boot monotonic samples degrade rather than producing negative age.

## Invariants

Lifecycle derives only from supplied signals and clock.

## Tests

This file.

## Open questions

None.

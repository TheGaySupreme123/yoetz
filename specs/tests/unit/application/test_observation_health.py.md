# tests/unit/application/test_observation_health.py

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `application/observation_health.md`

## Purpose

Prove ACTIVE/DEGRADED/STALE/STOPPED lifecycle rules with an injected monotonic clock.

## Cases

- Active when mapped, draining, and fresh
- Stale when progress exceeds threshold
- Single historical event does not stay active
- Stopped without consent
- Degraded with pending outbox or material gap

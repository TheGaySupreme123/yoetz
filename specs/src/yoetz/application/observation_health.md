# src/yoetz/application/observation_health.py — truthful observation lifecycle

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `domain/observation.py.md` |
**Imported by:** local/memory/sqlite observation status projection

## Purpose

Compute `ObservationLifecycle` from real acquisition, outbox, drain, mapping, and freshness
signals. A single historical event must never keep status `active` indefinitely.

## Public surface

- `ObservationHealthSignals`, `ObservationHealthThresholds`
- `compute_observation_lifecycle(...)`
- `qualifying_progress_monotonic(...)`

## Behavior

- `ACTIVE`: consent active, mapping available, sources present, no material gaps, pending outbox
  drained, and qualifying progress within freshness threshold.
- `DEGRADED`: running with material source/mapping/drain/gap issues.
- `STALE`: no qualifying progress within threshold or lag exceeds cap.
- `STOPPED`: consent absent/revoked/paused or session ended.

Tests inject the monotonic clock via `now_monotonic`.

## Errors and edge cases

Persisted monotonic samples from another boot epoch are incomparable and degrade until fresh
progress; negative age never crashes status.

## Invariants

1. Thresholds are owned by the observation subsystem.
2. Env-var markers never imply freshness.

## Tests

`tests/unit/application/test_observation_health.py`

## Open questions

None.

# tests/capability/test_session_event_monitor.py — session-lock/suspend capability probe

**Wave:** C/F | **ADRs:** ADR-008 | **Imports (spec-tree):** `src/yoetz/adapters/session_events.md` | **Imported by:** release evidence

## Purpose

Prove certified platform event subscription and monitor-loss detection.

## Public surface

Disposable event/backend probe producing structural evidence.

## Behavior

Validate normalized lock/suspend/resume/unlock ordering and disconnect-to-monitor-lost behavior.

## Errors and edge cases

Unavailable session bus/API, permission denied, duplicate/reordered events.

## Invariants

1. Ready claim requires active monitor evidence.
2. Resume never maps to ready.

## Tests

This file emits bounded structural capability evidence.

## Open questions

None.

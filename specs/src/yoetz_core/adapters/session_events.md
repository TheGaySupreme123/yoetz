# src/yoetz_core/adapters/session_events.py — user-session lock and suspend monitor

**Wave:** C | **ADRs:** ADR-008 | **Imports (spec-tree):** `service/lifecycle.md`,
`ports/diagnostics.md` | **Imported by:** `service/daemon.md`

## Purpose

Normalizes certified macOS/Linux user-session lock and system-suspend events into mandatory service
relock signals without claiming unsupported platform behavior.

## Public surface

- `class SessionEventMonitor` with async `start(callback)`, `capability`, and `close`.
- Platform backends for macOS user notification/session APIs and Linux login1 user-session D-Bus.
- Structural capability/error types only.

## Behavior

Subscribe before ready publication; emit exact lock/suspend/unlock/resume/monitor-lost events.
Lock/suspend/monitor loss immediately calls lifecycle drain. Unlock/resume never unlocks. Backend
disconnect becomes `monitor_lost`, not silent success. No shell polling or private user/session data
in diagnostics.

## Errors and edge cases

Failure to positively start the advertised platform monitor keeps the service locked with bounded
reason. Duplicate/reordered events are idempotently normalized; close removes subscriptions.

## Invariants

1. Ready service has a positively active session monitor on advertised targets.
2. Monitor loss relocks; resume never readies.
3. No event contains user content or secret.

## Tests

- `tests/integration/service/test_locked_ready_transitions.py` injects event/race matrices.
- `tests/capability/test_session_event_monitor.py` proves release-platform APIs.

## Open questions

None.

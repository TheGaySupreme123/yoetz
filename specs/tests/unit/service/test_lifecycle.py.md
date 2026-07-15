# tests/unit/service/test_lifecycle.py — service lifecycle state-machine unit suite

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `src/yoetz/service/lifecycle.md` | **Imported by:** test runner

## Purpose

Exhaustively verify legal transitions, admission accounting, idle relock, and bounded drain.

## Public surface

State-table, fake-clock, admission-counter, signal/event, concurrent lock/stop, and deadline tests.

## Behavior

Cover every state edge; quiescence excludes connected/in-flight/queued/leased/provider/secret work; wake never readies; monitor loss relocks.

## Errors and edge cases

Illegal transition, duplicate event, deadline termination, stale admission, and disabled-idle authorization.

## Invariants

1. Locked has zero ready-only capabilities.
2. False relock after deadline is impossible.

## Tests

This file is the executable owner.

## Open questions

None.

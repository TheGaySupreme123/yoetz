# tests/unit/service/test_lifecycle.py — service lifecycle state-machine unit suite

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `src/yoetz/service/lifecycle.md` | **Imported by:** test runner

## Purpose

Exhaustively verify legal transitions, admission accounting, idle relock, and bounded drain.

## Public surface

State-table, fake-clock, admission-counter, signal/event, concurrent lock/stop, exact idle-policy
target/proof, and deadline tests.

## Behavior

Cover every state edge; quiescence excludes connected/in-flight/queued/leased/provider/secret work;
wake never readies; monitor loss relocks. Freeze the target-digest domain, accept finite 60..86400
or explicit disabled only through a matching vault-minted proof, reject replay/race/wrong purpose,
preserve explicit/session/suspend/monitor relock, and restore 900 seconds on simulated restart.
Assert ready transitions require a positive vault generation, relock clears it, and proof
consumption receives the exact current service/vault generations, `policy_generation=None`, and
the one injected fake-clock sample.

## Errors and edge cases

Illegal transition, duplicate event, deadline termination, stale admission, and disabled-idle authorization.

## Invariants

1. Locked has zero ready-only capabilities.
2. False relock after deadline is impossible.

## Tests

This file is the executable owner.

## Open questions

None.

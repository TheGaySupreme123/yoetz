# tests/subprocess/test_kill_matrix.py — complete 16-boundary durability proof

**Wave:** C–F | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
child/fault helpers, storage/object/check/recovery specs | **Imported by:** nightly and release profile

## Purpose

Exercise abrupt death at every durability boundary and prove acknowledged state, idempotent recovery,
object/operation consistency, semantic freshness, migration/backup safety, and replay equivalence.

## Public surface

One parameter record per 16 named fault points declares operation fixture, fault mode, expected
pre/post-commit ambiguity class, recovery command, durable-state oracle, and platform applicability.

## Behavior

For each point, install candidate in a fresh environment, create deterministic baseline, arm exact
marker, invoke one operation, and trigger abrupt fault. Preserve raw stream digests; reopen only via
normal startup. Record frontier/object/operation state, retry identical request, rebuild projections
from zero, and compare with in-memory reference and no-fault control.

Points cover object stage/fsync/rename/dir-fsync, sequence/event/projection/operation transaction,
pre/post commit, MCP delivery, checkpoint, backup manifest, migration/restore route switch, provider
result persistence, and semantic freshness validation. Pre-commit may have no effect/garbage only;
post-commit returns the one durable result; maintenance exposes old or new complete generation;
semantic late/stale output never becomes selected finding.

Assert chains/digests, no partial batch, referenced objects present/authentic, no acknowledged
missing data, operation state legal, backup/migration recoverable, receipt wording coverage-bounded,
and privacy canaries absent from structural surfaces.

## Errors and edge cases

- Tests are serial per bundle and bounded by platform-specific release time/disk limits.
- A marker not reached, child not killed, cleanup failure, or timeout is a failed matrix cell.
- Automatic retry of the pytest case is forbidden; deterministic request retry is the subject.
- Fault hooks must be absent/denied in release configuration outside the certified test build path.

## Invariants

1. No acknowledged effect disappears and no retry duplicates it.
2. Object references never commit before object durability.
3. Canonical event bytes survive maintenance/replay unchanged.
4. Selected semantic output is durable and fresh.
5. Every advertised platform passes all applicable points.

## Tests

The nightly release profile runs all points across operations/platforms, validates redacted evidence
completeness, and separately proves the no-fault controls and production hook denial.

## Open questions

None.

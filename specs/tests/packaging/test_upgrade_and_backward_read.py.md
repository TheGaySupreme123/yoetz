# tests/packaging/test_upgrade_and_backward_read.py — prior-release data upgrade preservation

**Wave:** C/F | **ADRs:** ADR-002, ADR-003, ADR-006, ADR-007 | **Imports (spec-tree):** migration,
recovery, replay, old-fixture and package specs | **Imported by:** release upgrade claim

## Purpose

Prove every supported prior artifact/bundle can be read or safely migrated by the candidate while
preserving canonical event bytes, objects, identities, coverage, findings, responses, and receipts.

## Public surface

Matrix: each supported old release × advertised platform × normal upgrade, interrupted migration,
rollback/restore, newer-than-candidate schema, and retained old protocol/event versions.

## Behavior

Install prior artifact from immutable fixture wheel, create/use its golden bundle, and record exact
canonical event/object/frontier/projection/receipt digests. Copy fixture for candidate path; install
candidate, require verified backup and exclusive maintenance generation, run migration, reopen,
replay from zero and compare semantic state plus unchanged canonical bytes/digests.

Execute new candidate operation after upgrade and verify chain continuity. Inject migration failure
and kill near route switch; original or verified backup remains usable and no hybrid is routed.
Documented rollback restores data with prior artifact only when format policy permits; it never
down-migrates canonical history. Newer unknown schema fails writes/read policy honestly.

## Errors and edge cases

- Tests never mutate the sole golden fixture; use verified copies.
- Missing key/object/corrupt old fixture is a distinct negative failure, not migration behavior.
- Unsupported old version is explicitly rejected and cannot be advertised.
- Migration SQL never rewrites canonical event bytes.

## Invariants

1. Supported upgrades preserve canonical history and derived truth.
2. Backup precedes schema mutation.
3. Failure leaves an old or new complete recoverable state.
4. Unknown newer data is never written/downgraded.

## Tests

Retain one fixture/evidence record per released version forever; run full matrix before every release,
with fault subset in nightly and privacy scan of structural manifests.

## Open questions

None.

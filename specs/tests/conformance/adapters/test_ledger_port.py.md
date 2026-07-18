# tests/conformance/adapters/test_ledger_port.py — ledger port contract parity

**Wave:** A–F | **ADRs:** all | **Imports (spec-tree):**
`src/yoetz/ports/ledger.md`, `tests/conformance/protocol/test_idempotency_and_frontiers.py.md`
**Imported by:** conformance adapter tests

## Purpose

Prove the reference and durable ledger ports expose the same public behavior for append, freeze,
commit, and lookup operations.

## Public surface

- `test_append_batch_contract` — append behavior, conflicts, and idempotency match across backends.
- `test_load_and_freeze_contract` — event loading and frozen-case construction match across backends.
- `test_load_events_preserves_unknown_and_unavailable_records` — both variants retain every
  envelope field and use `payload=None` for redacted/key-unavailable objects.
- `test_append_warning_contract` — warnings are sorted-unique `AppendWarning` members and the sole
  v0.1 value is `unknown_event_schema_preserved`.
- `test_projection_query_contract` — exact filters, positions, and page metadata match; both
  backends reject `candidate_findings` as a row query.
- `test_projection_query_structural_index_and_hydration_contract` — all seven query views filter
  and seek on structural facts before opening only selected content rows.
- `test_projection_query_tombstone_and_compact_count_contract` — tombstones expose no invented
  item facts, produce exact gaps, and remain conservative in compact counts.
- `test_commit_check_if_current_contract` — check commit semantics match across backends.
- `test_frozen_case_lease_handoff_and_stale_dependency_contract` — each phase spends/replaces the
  embedded lease, and a final frontier/dependency mismatch terminally replays one
  `FRONTIER_CONFLICT` without events.
- `test_freeze_case_prepare_publish_reserve_contract` — both adapters build the case before object
  publication, keep expensive/object work outside the authoritative write section, and atomically
  revalidate every prepared identity before installing the resume pointer.
- `test_freeze_case_crash_and_resume_uses_stored_object` — a pre-reservation crash leaves no
  operation, while a post-reservation reclaim opens the exact stored object without rebuilding or
  republishing it.
- `test_lookup_operation_contract` — operation lookup and replay visibility match across backends.

## Behavior

The test runs each port scenario against memory and SQLite backends with the same injected clocks,
IDs, and policy/provider scripts. It asserts:

- equal logical input yields equal public result;
- canonical bytes, digests, and frontier transitions are stable;
- `FrozenCase` has exactly `case` and the current `lease`; terminal success replay is a
  `CheckCommitResult`, never a nullable-lease frozen case;
- `CheckCommitResult` maps to the public `CheckSuccessModel` without exporting its internal
  `outcome` or colliding with the public `CheckResult` alias;
- known/unknown `LedgerRecord` values preserve ancestry/refs/digests and unavailable payloads are
  exactly `None`;
- projection pages compare their typed raw items, exact frontiers/lag/version/gaps, and exclusive
  typed next position; no cursor bytes or privacy projection enter the adapter oracle;
- every optional filter subset, mixed-direction finding rank seek, ID/sequence seek, historical
  visibility interval, and edge-table join returns byte-equal pages without payload-assisted
  filtering;
- a selected unreadable row advances the structural position without replacement, lookahead stays
  unopened, and arbitrary pages concatenate every renderable row exactly once;
- check scope/policy-execution/suppression/coverage/semantic facts produce the same monotonic
  finding resolution in both adapters; response disposition and waiver expiry are irrelevant;
- instrumented freeze hooks establish the strict order `prepare -> build_case ->
  finalize_object -> final_reservation`; the final reservation repeats idempotency, pending-import,
  head, projection identity, dependency digest, expected-frontier, and owner-generation checks;
- mutation of any revalidated fact between object finalization and reservation installs no
  object inventory or operation pointer, and two concurrent same-ID preparations can install at
  most one exact object reference (the other finalized object remains unreferenced);
- no accepted-record paging, replay/reducer work, canonicalization, hashing, encryption, fsync,
  object open, clock call, or ID allocation occurs while the final write transaction/shared lock
  is held;
- after a committed reservation, crash/reclaim reads and authenticates the row's stored object;
  injected builders/publishers must remain uncalled, and a missing or binding-mismatched object
  produces `operation_resume_object_invalid` rather than replacement;
- adapter diagnostics may differ, but public behavior does not;
- unsupported/private row details never become part of the comparison oracle.

## Errors and edge cases

- A port-specific shortcut that changes public output fails.
- A comparison that includes private row IDs instead of public artifacts fails.

## Invariants

1. Ledger port behavior is adapter-neutral.
2. Public canonical artifacts are the comparison oracle.
3. Private adapter details stay private.
4. Append warnings and all internal discriminated records retain their nominal enum/type identity.
5. Dependency staleness has one adapter-neutral terminal conflict outcome.
6. A resume object is built from its case before reservation, and a reservation never points to a
   pre-case or unverified object.
7. Projection-query indexes contain only the closed nonplaintext facts and never need payload text
   to match or order a row.

## Tests

- `tests/conformance/adapters/test_ledger_port.py`

## Open questions

None.

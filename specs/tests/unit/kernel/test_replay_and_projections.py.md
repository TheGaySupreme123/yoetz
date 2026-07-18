# tests/unit/kernel/test_replay_and_projections.py — replay parity and projection digest stability

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/reducers.md`, `src/yoetz/kernel/projections.md`
**Imported by:** the kernel unit suite

## Purpose

Prove that full replay, incremental replay, and stored projection snapshots all converge on the same
derived state.

## Public surface

- `test_empty_full_incremental_replay_match` — replay strategies land on the same state.
- `test_projection_snapshot_order_is_stable` — snapshot key ordering is deterministic.
- `test_projection_digest_is_hash_seed_and_locale_stable` — digest bytes do not drift.
- `test_check_suppressed_count_survives_replay_and_snapshot` — reducer and canonical snapshot
  preserve the exact returned IDs/count.
- `test_projection_record_and_snapshot_shapes_are_exact` — every common/specialized record,
  contradiction key, integer rendering, and top-level field matches the frozen fixture shape.
- `test_redacted_payload_deletion_rebuilds_identically_from_locator` — deleting targeted payload
  objects and disposable projections still yields the same tombstones, gaps, digest, and secondary
  effects from accepted records plus durable locators.
- `test_object_only_payload_and_captured_content_redactions_converge` — an object target resolves
  independently through payload-object ownership and evidence captured-object association without
  retaining either plaintext body.
- `test_repeated_object_redaction_keeps_first_cause_root` — one object marker keeps the earliest
  causative redaction event by ledger sequence as its bounded public root.
- `test_gap_marker_lifecycle_is_exact` — all four marker grammars have exact roots/codes,
  append/recompute behavior, and deterministic removal.
- `test_contradiction_replacement_and_redaction_are_directional` — complete claim subject edges are
  replaced by claim identity and redaction removes only source-owned edges.
- `test_corruption_requires_rebuild` — broken snapshots are rejected rather than patched.

## Behavior

The suite checks:

- replay from `empty_projection_state()` matches replay from partitioned prefixes;
- projection snapshots preserve registry order and deterministic map ordering;
- the 17-field snapshot uses decimal strings only for registered frontier/version fields, retains
  JSON integers for suppression/payload integers, and exposes no locator metadata;
- common and specialized records preserve payload digests and exact null-payload tombstones;
- full replay after physical payload deletion equals the incremental pre-deletion/redaction path in
  REP-003, including plan/decision/claim/check secondary-effect removal;
- a generated object-only stream separately targets (a) a current claim payload object, (b) a
  current evidence captured-content object, and (c) one object occupying both associations; the
  incremental path and a full replay with targeted payload objects absent produce byte-identical
  tombstones, evidence availability, secondary effects, markers, snapshots, and digests;
- `ReplayIndex` genesis/extend/frontier validation, unique payload-object ownership, exact
  evidence empty-or-singleton artifact mirror, nonplaintext field inventory, restart rebuild from
  envelope/locator rows, and rejection of a future-complete index are all exercised;
- unknown/redacted markers persist, `missing_ref` markers recompute from current visible sources,
  and later companions/source replacement/source redaction remove exactly the obsolete marker;
- two redaction events targeting the same captured object retain one object marker whose typed gap
  root remains the first event by ingestion sequence under every hash seed, even when its random
  event ID sorts later; reversing input order is rejected as a ledger-order violation, not normalized;
- directed contradiction keys use the collision-free pipe form and claim republishing replaces the
  complete old edge set without a prose-based resolution inference;
- `check_recorded -> latest_tested_state -> projection_snapshot -> replay` preserves the exact
  `returned_finding_ids` and `suppressed_count`, including a nonzero count;
- digests remain stable across interpreter seed and locale variants;
- a corrupt or stale projection forces a rebuild path instead of silent repair.

## Errors and edge cases

- A digest change without an input change fails the test.
- A replay that depends on sorting the ledger stream fails the test.
- A missing/mismatched locator, wrong logical key/digest/target set, noncontiguous ledger sequence,
  or predecessor/head mismatch stops replay rather than weakening or repairing it.
- A missing evidence mirror, duplicate payload-object owner, index from the wrong frontier, or
  object marker with no causative redaction locator stops replay/case construction.
- An implementation that retains locator fields in projection snapshots or payload prose in the
  durable sidecar fails the test.

## Invariants

1. Replay is deterministic.
2. Snapshot bytes are canonical.
3. Corruption is explicit, not patched over.
4. Full and incremental replay converge after redaction and physical payload deletion.
5. Coverage gaps and contradiction edges have one exact structural identity and lifecycle.

## Tests

- `tests/unit/kernel/test_replay_and_projections.py`

## Open questions

None.

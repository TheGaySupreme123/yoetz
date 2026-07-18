# tests/integration/storage/test_migration_0001.py — first migration and schema identity

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz/adapters/sqlite/migrations.md`, `src/yoetz/version.md`
**Imported by:** integration storage tests

## Purpose

Prove the first durable migration creates the exact v0.1 schema identity and is idempotent on a
fresh bundle.

## Public surface

- `test_fresh_migration_creates_required_tables_and_indexes` — the full v0001 structure appears.
- `test_migration_is_idempotent_on_reopen` — rerunning on the same bundle is safe.
- `test_wrong_application_id_or_version_fails_closed` — mismatched storage identity is rejected.
- `test_rollback_leaves_original_bundle_usable` — partial migration failure does not corrupt the
  source bundle.
- `test_catalog_privacy_tables_and_pin_root_columns_are_exact` — privacy policy/audit/root tables,
  indexes, and bundle-pin privacy-root fields match the frozen DDL.
- `test_event_projection_locator_table_is_exact` — every accepted event has one strict durable
  structural locator row and the table is outside disposable projection generations.
- `test_projection_identity_is_text_not_generation` — `projection_version` stores
  `yoetz/0.1.0` as text while generation remains the separate integer `1`.
- `test_generation_one_projection_schema_is_exact` — all fourteen current-record plus seventeen
  temporal-query `p1_` tables, their ordered columns, constraints, foreign keys,
  STRICT/WITHOUT-ROWID flags, and forty-six named indexes match the released normalized SQL.
- `test_empty_projection_rows_are_seeded_exactly` — initialization writes the single active
  identity row and matching empty generation-1 top-level state, with no record/gap rows.
- `test_latest_check_shape_is_all_or_none` — each partial latest-check shape is rejected and the
  exact canonical Coverage/finding-ID blobs round-trip only when every field is present.
- `test_projection_records_use_encrypted_source_objects` — record tables have structural locator
  columns and no payload/body/free-form-content column; readable bodies are joined through the
  source event's encrypted object and tombstones never open it.
- `test_projection_link_and_gap_constraints_are_exact` — replacement, supersession,
  contradiction, evidence-object, result-action, four gap branches, and first-cause redaction roots
  preserve their registered structural identities and reject invalid branch mixtures.
- `test_logical_missing_refs_are_representable` — result-action, response-finding,
  contradiction-target, and obligation-replacement targets may be absent while source
  event/locator/object foreign keys still fail closed.
- `test_replay_index_associations_are_reconstructible_without_payloads` — the unique payload-object
  index, artifact-ref mirror, and locator target arrays rebuild all three reverse maps after the
  referenced object bytes are deleted.
- `test_base_access_indexes_are_exact_and_bounded` — ledger sequence/ref, semantic-operation, and
  importer alias/status/terminal/next-batch paths exist with the frozen names and column order.
- `test_projection_query_index_is_temporal_nonplaintext_and_covering` — every query view has exact
  validity/filter/order facts, finite edges/fanout, and a named bounded access path.
- `test_projection_query_tombstones_scrub_all_intervals` — redaction clears payload-derived
  columns, edges, and finding-order fanout globally while retaining only minimal identity/counts.

## Behavior

The test checks that the migration:

- creates the reviewed schema objects and CHECK/STRICT/WITHOUT ROWID choices;
- creates exact policy history/transition, three-branch audit/local-disclosure, receipt-query, and
  live-root catalog objects without a content-bearing plaintext column;
- requires every maintenance pin to carry nonnegative `privacy_root_generation` and exact
  `privacy_root_digest` in addition to its frontier;
- creates `event_projection_locators` with the exact seven columns, primary/FK/STRICT/WITHOUT
  ROWID shape, one-row-per-event cardinality, canonical target-array bytes, and no content column;
- stores projection identity and generation in their distinct registered types and requires the
  one active name/version/generation triple `work`/`yoetz/0.1.0`/`1`;
- creates the fourteen current-record tables `p1_projection_state`, `p1_plans`, `p1_obligations`,
  `p1_obligation_replacements`, `p1_decisions`, `p1_assignments`, `p1_actions`, `p1_results`,
  `p1_evidence`, `p1_claims`, `p1_contradictions`, `p1_findings`, `p1_responses`, and
  `p1_coverage_gaps`, plus exactly `p1_query_snapshots`, `p1_query_assignments`,
  `p1_query_assignment_obligations`, `p1_query_obligations`,
  `p1_query_obligation_source_refs`, `p1_query_obligation_evidence_refs`,
  `p1_query_obligation_actors`, `p1_query_findings`, `p1_query_finding_subject_refs`,
  `p1_query_finding_order`, `p1_query_responses`, `p1_query_response_evidence_refs`,
  `p1_query_checks`, `p1_query_check_scope_refs`, `p1_query_check_policy_executions`,
  `p1_query_check_returned_findings`, and `p1_query_evidence`, with no extra generation-1 table;
- compares normalized `sqlite_schema.sql`, `PRAGMA table_xinfo`, `PRAGMA foreign_key_list`,
  `PRAGMA index_list`/`index_xinfo`, and `PRAGMA table_list` against one literal expected
  inventory, including all named partial indexes and WITHOUT-ROWID primary-key order;
- seeds `projection_state` and `p1_projection_state` at frontier `0`/`genesis`, the frozen empty
  projection digest
  `sha256:0f8ec0c66f196bee631ef5447ef5c914e812fe530ee1f4b7477e24b22a9911c9`, NULL latest check,
  NULL compact source/Coverage/gap fields, zero compact counters, `unknown` freshness, and zero
  unknown-event count; seeds the one matching open-ended `p1_query_snapshots` genesis interval and
  no other query row;
- loads readable record payloads only by following `source_event_id` to
  `events.payload_object_id`, authenticating/decrypting that object, and checking its locator
  digest/family/logical key/source frontier; a redacted row proves the object getter was not called;
- reconstructs the replay fixture's exact plan/obligation/decision/evidence/contradiction links and
  gap tuple from normalized rows, then recomputes the same 17-key projection snapshot/digest;
- proves compact/status fields and temporal query rows are excluded from the 17-key projection
  digest while independently matching the current typed projection and accepted-envelope Coverage;
- records the causative redaction event with the lowest ledger ingestion sequence as the one
  object-gap root and proves later redactions or event-ID ordering cannot replace it;
- requires the named unique `events_payload_object` index and rebuilds
  `payload_event_by_object`, `evidence_sources_by_object`, and `redaction_root_by_object` from
  `events`, `event_refs`, and `event_projection_locators` without reading object bytes;
- freezes `event_refs.ref_type` as exactly `artifact|evidence|result|finding|claim`, preserving a
  result-ID member of the envelope evidence-ref union under its own kind;
- compares the exact thirteen-name base access-path set and `PRAGMA index_xinfo` key-column order,
  then
  uses `EXPLAIN QUERY PLAN` fixtures to require the matching named index for session/schema/writer
  event scans, all four history schema/actor filter shapes with exclusive ingestion cursors,
  typed-ref reverse lookup, semantic operation/state lookup, source aliases, importer state/
  terminal status, and next planned batch;
- inserts version changes at several frontiers and proves the half-open predicate
  `valid_from_seq <= F AND (valid_to_seq IS NULL OR F < valid_to_seq)` selects exactly one snapshot
  and at most one structural version per logical row;
- requires assignment `resolved`, obligation effective status/current actors, finding disposition/
  resolved/full rank, and evidence availability/freshness to be SQL predicates before hydration;
  every owner/target edge lookup is indexed in both directions where registered;
- enumerates every optional-filter combination and uses `EXPLAIN QUERY PLAN` to require the exact
  assignment/obligation/evidence covering index, the finding fanout covering index with one
  lexicographic exclusive rank seek, and no full scan or temporary B-tree; each fixture selects at
  most `limit + 1` candidate keys and opens no payload while filtering;
- verifies unresolved finding fanout has exactly sixteen rows (eight for a resolved finding),
  wildcard sentinels cannot collide with registered enums/priority, and descending rank facts are
  stored only as their checked nonpositive query-order transforms;
- proves check applicability is reconstructible only from normalized scope, exact
  `run/completed` owner-policy execution, subject frontier, zero suppression, current/no-gap
  Coverage, semantic completion when required, and returned-finding/issue-key facts;
- proves a global redaction nulls actor/status/strength/rank/issue/scope/response/check payload facts
  and deletes every owned edge/fanout row across historical intervals; tombstone obligations and
  findings remain only in conservative compact counts;
- requires `events.occurred_at`, `projection_status`, and `summary_code` to agree with canonical
  entry/locator facts and returns history without opening a payload object;
- stamps the exact application and bundle schema identities;
- leaves no half-migrated writable bundle behind on failure;
- can be re-run on an already-migrated bundle without changing the identity.

The exact named generation-1 index set asserted by the test is:

```text
p1_plans_source_event
p1_plans_superseded_by
p1_obligations_source_event
p1_obligations_change_source_event
p1_decisions_superseded_by
p1_actions_source_event
p1_results_source_event
p1_results_action
p1_evidence_source_event
p1_claims_source_event
p1_contradictions_source_event
p1_findings_source_event
p1_responses_source_event
p1_coverage_gaps_root
p1_coverage_gaps_source
p1_coverage_gaps_target_object
p1_query_snapshots_visibility
p1_query_assignments_all
p1_query_assignments_resolved
p1_query_assignments_actor
p1_query_assignments_actor_resolved
p1_query_assignments_handoff
p1_query_assignment_obligations_target
p1_query_obligations_all
p1_query_obligations_status
p1_query_obligations_compact
p1_query_obligation_source_refs_target
p1_query_obligation_evidence_refs_target
p1_query_obligation_actors_actor
p1_query_obligation_actors_actor_status
p1_query_findings_issue
p1_query_finding_subject_refs_target
p1_query_finding_order_cover
p1_query_response_evidence_refs_target
p1_query_checks_whole_case
p1_query_check_scope_refs_target
p1_query_check_policy_applicability
p1_query_check_returned_findings_target
p1_query_evidence_all
p1_query_evidence_strength
p1_query_evidence_freshness
p1_query_evidence_strength_freshness
p1_query_evidence_available
p1_query_evidence_available_strength
p1_query_evidence_available_freshness
p1_query_evidence_available_strength_freshness
```

The exact named base access-path set asserted by the test is:

```text
events_session_seq
events_session_schema_seq
events_session_author_seq
events_session_schema_author_seq
events_schema_seq
events_writer_seq
events_payload_object
refs_target
semantic_jobs_operation_state
import_request_aliases_source
import_jobs_session_state
import_jobs_session_terminal
import_batches_next
```

The normalized-schema assertion ignores only SQLite's implementation-assigned internal root-page
numbers. It does not normalize away column order, constraint spelling, partial-index predicates, or
table options. The root reviewable migration and installed resource must produce the same expected
inventory; tests never create missing projection tables themselves.

## Errors and edge cases

- A migration that auto-heals a corrupt schema without failing is wrong.
- A rollback that leaves ambiguous state behind fails the test.
- A legacy privacy-blind pin row shape, incomplete catalog privacy table/index inventory, agent-
  projection content/object column, or invalid outcome/reason pair fails.
- A missing/extra/mismatched locator row, plaintext/free-form locator column, or integer
  `projection_version` fails.
- A missing/extra `p1_` table/index, renamed/retyped/reordered column, rowid table, weakened CHECK,
  dangling source/storage foreign key, partial latest check, noncanonical structural blob,
  source-frontier mismatch, invalid gap branch, or projection digest mismatch fails. A missing
  logical companion is not a dangling storage foreign key; the matching `missing_ref` row is
  required instead.
- A query version with an overlapping/invalid interval, more than one visible version, a tombstone
  retaining any payload-derived fact/edge/order row, a missing compact counter, or a query plan
  that decrypts/skips unbounded rows fails.
- A `redacted_object` marker with no root, a root whose locator does not target the object, a later
  rather than first causative ingestion, or a root beyond the materialized frontier fails.
- A duplicate event payload-object association, missing reverse index, or replay-index rebuild that
  opens an object fails.
- A missing/renamed base access index, reordered key column, full scan, or temporary sort for its
  registered lookup fixture fails even if the query would return the same rows on a small bundle.
- A projection column named or serving as `payload`, `body`, `canonical_json`, `description`,
  `statement`, free-form response reason, `command`, or `summary` fails even if its declared SQLite
  type is BLOB. The exact structural columns `payload_digest`, `semantic_reason`, and
  `execution_reason` are required digest/closed-enum facts and are not body-content columns.

## Invariants

1. Migration identity is explicit.
2. Failure leaves a usable or quarantined prior state.
3. Reopen is idempotent.
4. Schema identity includes privacy audit reachability and cannot regress to ledger-only roots.
5. Redaction-safe replay metadata is durable structural state, not a disposable projection cache.
6. Projection identity text, generation integer, top-level state, record links, and gap rows are one
   exact release schema rather than adapter-created implementation choices.
7. Disposable projections may point to encrypted payload truth; they never duplicate payload
   plaintext into SQLite structural columns.

## Tests

- `tests/integration/storage/test_migration_0001.py`

## Open questions

Materialization is gated by `specs/OPEN_QUESTIONS.md` `W-C-001`. The first test must execute the
single root `migrations/bundle/0001.sql` resource on a fresh database; a harness that precreates
missing importer/base tables or concatenates SQL fragments from owner specs is not acceptable exit
evidence.

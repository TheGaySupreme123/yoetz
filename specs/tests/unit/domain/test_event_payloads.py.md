# tests/unit/domain/test_event_payloads.py — event payload validation and immutability

**Wave:** A/B | **ADRs:** ADR-002, ADR-003, ADR-005 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/values.md`, `src/yoetz/protocol/models.md`
**Imported by:** the domain unit suite

## Purpose

Lock the family-by-family payload contracts for accepted events so each event shape stays exact and
immutable.

## Public surface

- `test_each_event_family_validates_required_and_optional_fields` — every family accepts the
  reviewed happy path and rejects one missing-required example.
- `test_event_payloads_are_frozen` — payloads do not mutate after construction.
- `test_boundary_model_conversion_normalizes_optional_fields` — schema-valid absent/empty optional
  collections decode to one domain value and emit the registered absent form.
- `test_subject_state_and_reference_fields_remain_bounded` — evidence and state refs are exact.
- `test_generated_domain_payload_encode_decode_is_byte_stable` — Hypothesis-generated domain
  payloads encode/decode/re-encode to identical normalized canonical bytes across determinism
  controls.
- `test_exact_schema_pair_dispatch_and_unknown_boundary` — only the sixteen complete
  `(name, "1.0.0")` pairs decode; every other valid pair takes the opaque path and malformed known
  payloads never do.
- `test_support_types_and_client_enum_identity_are_exact` — all support enums/records have the
  frozen shapes, and the event module re-exports the protocol-owned client enum objects by identity.
- `test_accepted_record_views_are_exact` — full accepted JSON has exactly 19 fields and the digest
  preimage differs only by removing `entry_digest`; neither view includes payload/locator metadata.
- `test_projection_locator_is_bounded_and_nonplaintext` — every family uses the exact logical-key
  mapping/digest/target rules and invalid or text-bearing sidecars fail closed.
- `test_object_redaction_envelope_mirrors_are_exact` — payload objects, evidence captured objects,
  and redaction targets are recoverable from the existing 19-field envelope without a plaintext
  locator extension.
- `test_check_payload_records_normalized_scope_and_policy_executions` — the current write shape
  requires a normalized direct scope and one exact execution record per selected built-in pack.
- `test_session_opened_preserves_full_start_content_and_independent_history_refs` — all three raw
  identity strings preserve the 8,192-code-point request bound and imported history may carry one
  optional ref, while the Start application separately enforces both-or-neither.
- `test_check_payload_provenance_matches_selected_final_outcome` — any present finalized
  provenance repeats the top-level status/reason and earlier attempt outcomes cannot replace it.
- `test_envelope_evidence_refs_preserve_evidence_and_result_ids` — response/result mirrors retain
  the exact sorted-unique `EvidenceId | ResultId` union in drafts and accepted records.

## Behavior

The suite covers all event families in the registry and checks:

- required/optional field presence;
- family-specific enum/value validation;
- frozen dataclass behavior;
- exact normalization between schema-valid boundary values and domain payload shapes, including
  explicit-empty versus absent optional collections;
- one dedicated rejection per family invariant;
- complete schema-pair dispatch, supported-version mismatch, and malformed-known behavior;
- constructor order, enum ownership, chain/payload-ref bounds, and exact record JSON views;
- locator logical keys for all sixteen families, redaction-target exclusivity, schema/digest
  matching, and rejection via `invalid_projection_locator` without retaining payload prose;
- every payload object maps through `payload_ref.object_id`; exact-known evidence uses only
  `artifact_refs == ()|(captured_object_id,)`; redaction uses
  `artifact_refs == target_object_ids`; receipts use exactly
  `artifact_refs == (receipt_object_id,)`; unknown/non-evidence artifact refs never enter the
  evidence reverse index; and the accepted JSON field count remains exactly 19;
- generated **domain** payload encode/decode/re-encode identity for every family, including Unicode and
  boundary sizes, under multiple `PYTHONHASHSEED`, locale, and timezone controls.
- `check_recorded` requires nonempty canonical `policies`, a required scope whose two typed ID
  tuples are sorted unique (empty/empty means whole case), and required `policy_executions` with
  exactly the same pack identity/version/order. Every legal outcome/reason pair round-trips;
  missing fields, zero packs, cross-pack attribution, reordering, duplicate or unsorted scope IDs,
  and illegal outcome/reason pairs fail.

## Errors and edge cases

- Unknown family names are not accepted as generic payloads.
- A known name at another version is unknown, while an exact known pair with malformed content is
  invalid and is never preserved as opaque.
- Payloads cannot mutate after validation.
- A locator containing free text, the wrong logical key/schema/digest, or redaction targets on a
  non-redaction family is invalid.
- An evidence envelope with an extra/missing captured-object mirror, a redaction envelope whose
  artifact refs differ from object targets, a receipt envelope without its exact singleton document
  mirror is invalid at this value boundary. Duplicate ownership of one payload object is aggregate
  `ReplayIndex` corruption and is exercised by the reducer suite that owns bundle-wide identity.
- A generated strategy that omits a family or filters away boundary cases fails the suite.
- A current check event cannot infer scope or execution from verdict, findings, or the legacy
  compatibility shape; released backward-read archive bytes are exercised only by the compatibility
  reader and are never accepted as a new current write.

## Invariants

1. Event payloads are family-specific and frozen.
2. No family silently widens its contract.
3. Boundary conversion preserves exact meaning through one documented normalized representation.
4. Accepted domain payloads have one stable canonical byte representation independent of environment.
5. Replay metadata remains structural, local, nonplaintext, and outside both accepted wire views.

## Tests

- `tests/unit/domain/test_event_payloads.py`

## Open questions

None.

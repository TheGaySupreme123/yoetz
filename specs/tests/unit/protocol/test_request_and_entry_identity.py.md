# tests/unit/protocol/test_request_and_entry_identity.py — request and ledger-entry identity rules

**Wave:** A/B | **ADRs:** ADR-002, ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/publish_work.md`, `src/yoetz/application/respond.md`,
`src/yoetz/domain/events.md`, `src/yoetz/protocol/canonical.md`, `tests/fixture_loader.py`,
`fixtures/manifest.json`
**Imported by:** the protocol and boundary unit suite

## Purpose

Prove that logical request identity and ledger entry identity are derived from the correct fields
and exclude transport-only or generated values.

### Implementation ordering

This is intentionally a cross-wave integration test. Its publish/respond request-identity cases are
collected only after the B1 domain event owner and the B6 application owners named above exist; B0
must not create placeholders or reverse the dependency graph to make this file importable early.
The B0 `entry_digest` preimage and canonical-byte behavior is exercised with the canonical protocol
vectors during B0, then this complete file becomes collectible at the later owning wave.

## Public surface

- `test_publish_work_request_digest_excludes_generated_fields` — generated object IDs, nonces, and
  acceptance-only fields do not affect the logical request digest.
- `test_accepted_entry_digest_covers_structural_envelope` — accepted-entry identity includes the
  structural envelope actually committed.
- `test_accepted_record_and_digest_preimage_are_distinct_views` — the full record includes
  `entry_digest`, the preimage removes it, and neither serialized view includes decoded `payload`.
- `test_entry_digest_rejects_non_preimage_views` — embedded `entry_digest`, embedded decoded
  `payload`, missing/extra structural fields, wrong protocol, and non-mapping inputs fail with
  `not_an_accepted_envelope`.
- `test_payload_ref_plaintext_size_is_bounded_json_integer` — schema/model accept boundary integers
  and reject strings, booleans, fractions, and out-of-range values.
- `test_replayed_logical_identity_is_stable` — canonical-equivalent retries produce the same
  identity.
- `test_idempotency_conflict_is_identity_conflict_not_payload_diff` — a reused request ID with a
  changed logical identity is a conflict.

## Behavior

The suite locks the boundary between logical request semantics and generated/envelope details:

- request identity is built from caller-owned semantics only;
- generated IDs, object envelopes, timestamps, and transport framing do not leak into the logical
  digest;
- `accepted_record_to_json()` is the schema-shaped persisted record: it includes the computed
  `entry_digest` and excludes the decoded in-memory `payload` handle;
- `accepted_record_digest_preimage()` removes exactly `entry_digest` from that record and
  `entry_digest()` hashes that preimage; all other structural fields, including `payload_ref`,
  remain covered;
- `entry_digest()` itself owns only the exact 18-key top-level set, the
  `protocol == "yoetz.event"` token, and canonical-value validation. Nested accepted-event
  field/domain validity is established by `accepted_record_digest_preimage()` and the frozen schema,
  not reimplemented by the digest helper;
- the accepted-event JSON Schema validates the full record only, not the deliberately incomplete
  digest preimage;
- `payload_ref.plaintext_size` remains a bounded JSON integer exactly as released in Wave A; it is
  not converted to the canonical integer-string representation used by sequence/frontier IDs;
- equivalent logical requests must stay stable across retry and replay.

## Errors and edge cases

- If a generated field affects the logical digest, the contract is too weak.
- If transport framing changes the accepted-entry digest, replayability is broken.
- If hashing accepts a full record with an embedded digest or any decoded payload field, the
  preimage boundary is ambiguous and the test fails.
- Golden CAN-006/CAN-007 bytes are loaded from root `fixtures/` through the shared manifest-bound
  loader; Markdown shadows and installed mirrors are not alternate inputs.

## Invariants

1. Logical identity excludes generated envelope noise.
2. Structural entry identity covers what the ledger actually accepted.
3. Retry stability is a correctness requirement, not a convenience.

## Tests

- `tests/unit/protocol/test_request_and_entry_identity.py`

## Open questions

None.

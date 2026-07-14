# tests/unit/protocol/test_request_and_entry_identity.py — request and ledger-entry identity rules

**Wave:** A/B | **ADRs:** ADR-002, ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/application/publish_work.md`, `src/yoetz_core/application/respond.md`,
`src/yoetz_core/domain/events.md`, `src/yoetz_core/protocol/canonical.md`
**Imported by:** the protocol and boundary unit suite

## Purpose

Prove that logical request identity and ledger entry identity are derived from the correct fields
and exclude transport-only or generated values.

## Public surface

- `test_publish_work_request_digest_excludes_generated_fields` — generated object IDs, nonces, and
  acceptance-only fields do not affect the logical request digest.
- `test_accepted_entry_digest_covers_structural_envelope` — accepted-entry identity includes the
  structural envelope actually committed.
- `test_replayed_logical_identity_is_stable` — canonical-equivalent retries produce the same
  identity.
- `test_idempotency_conflict_is_identity_conflict_not_payload_diff` — a reused request ID with a
  changed logical identity is a conflict.

## Behavior

The suite locks the boundary between logical request semantics and generated/envelope details:

- request identity is built from caller-owned semantics only;
- generated IDs, object envelopes, timestamps, and transport framing do not leak into the logical
  digest;
- accepted entry identity must cover the exact structural record committed, not the user prompt or
  surrounding transport;
- equivalent logical requests must stay stable across retry and replay.

## Errors and edge cases

- If a generated field affects the logical digest, the contract is too weak.
- If transport framing changes the accepted-entry digest, replayability is broken.

## Invariants

1. Logical identity excludes generated envelope noise.
2. Structural entry identity covers what the ledger actually accepted.
3. Retry stability is a correctness requirement, not a convenience.

## Tests

- `tests/unit/protocol/test_request_and_entry_identity.py`

## Open questions

None.

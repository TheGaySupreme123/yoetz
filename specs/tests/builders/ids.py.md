# tests/builders/ids.py — explicit identifier builder helpers

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/INTERFACES.md`, `specs/tests/fixture_loader.py.md`, `specs/tests/builders/__init__.md` |
**Imported by:** unit/property/integration/conformance fixtures

## Purpose

Provide deterministic ID construction helpers for test data. The helpers must make the ID kind and
seed explicit so tests do not hide correctness-relevant choices.

## Public surface

- `IdFamily` — exactly
  `request|task|session|writer|event|obligation|claim|action|result|evidence|finding|object|receipt`.
- `Seed = str | bytes`; `PREFIX_BY_FAMILY` is the immutable prefix map for those thirteen families.
- `build_id(family: IdFamily, seed: Seed, /) -> str` and
  `validate_test_id(family: IdFamily, value: object, /) -> str`.
- Convenience helpers `request_id`, `task_id`, `session_id`, `writer_id`, `event_id`,
  `obligation_id`, `claim_id`, `action_id`, `result_id`, `evidence_id`, `finding_id`, `object_id`,
  and `receipt_id`, each requiring an explicit `Seed`.
- `operation_id(seed: Seed, /) -> str` — an intentional alias for `request_id(seed)` because the
  durable operation identity is the request ID, not a separate identifier family.
- `entry_digest(digest_preimage: bytes, /) -> str` — a deterministic SHA-256 digest helper;
  ledger entries have digests and do not have an `entry` ID family.

## Behavior

Every helper requires explicit caller-supplied inputs for the kind-specific pieces that affect the
result. The module never invents a seed, falls back to ambient randomness, or shares mutable global
state. It produces canonical IDs only, and the same explicit inputs always yield the same output.
`build_id` hashes `b"yoetz/test-id/v1\x00" + family-ascii + b"\x00" + seed-bytes`, takes the
first sixteen digest bytes, forces RFC 4122 version-4 and variant bits, and prepends the registered
prefix. `entry_digest` hashes the supplied complete digest preimage bytes and renders
`sha256:<64 lowercase hex>`; its parameter is not a canonical-envelope claim because callers may
prepare the exact entry preimage before passing it.

These builders cover only the thirteen test families listed above. They do not create actor IDs,
do not add `operation` or `entry` to `IdFamily`, and expose no ID parser or reverse-prefix API.

## Errors and edge cases

- Missing required inputs, wrong kind, or malformed seed data fails closed.
- The module must not normalize hostile input into a different valid ID.
- `operation_id` must remain byte-for-byte identical to `request_id` for the same seed.
- An empty or non-bytes digest preimage fails rather than being coerced.

## Invariants

1. ID builders are deterministic.
2. No hidden defaults stand in for correctness-relevant values.
3. Hostile input is rejected, not repaired.
4. Operation identity is a request-ID alias; ledger entry identity is its digest.

## Tests

- `specs/tests/unit.md`
- `specs/tests/property.md`
- `specs/tests/integration.md`

## Open questions

None.

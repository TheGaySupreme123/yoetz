# tests/integration/objects/test_portable_recovery.py — portable recovery bundle behavior

**Wave:** C/D | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/adapters/objects/encrypted_files.md`, `src/yoetz_core/adapters/keys/passphrase.md`
**Imported by:** integration object/key tests

## Purpose

Prove the portable backup and recovery artifacts can be moved and restored on a clean machine
without hidden state.

## Public surface

- `test_portable_bundle_replays_on_clean_profile` — recovery works from the portable artifact.
- `test_nonportable_state_is_not_assumed` — machine-bound data is not treated as portable.
- `test_recovered_artifacts_match_digests` — restored bytes match the manifest.
- `test_create_double_entry_restore_single_entry` — create compares two exact buffers but sends one
  handle; restore accepts one entry, with crossed operation bindings rejected.
- `test_recovery_envelope_known_answers_and_parameter_caps` — exact Argon2id/HKDF/RFC3394/HMAC/JCS/
  base64url bytes interoperate and hostile parameter fields fail before unbounded KDF work.

## Behavior

The test restores a reviewed backup bundle under a clean profile and checks:

- all required objects decrypt;
- digests and manifest entries match;
- machine-bound assumptions are not required for success;
- any missing portability claim is surfaced as a limitation.
- strict UTF-8/length/forbidden-codepoint and non-normalization vectors are identical for wrap and
  unwrap.
- exact candidate/boundary Argon2id parameters, 32-byte salt, subkey infos, 40-byte wrapped BMK,
  binding, authentication tag, canonical envelope and artifact digest match CAN-009;
- unknown/duplicate fields, noncanonical encoding, binding/tag/wrap/trailing-byte mutation, wrong
  secret, and KDF values outside the closed caps never return a recovered key handle.

## Errors and edge cases

- A recovery that depends on undeclared local state fails.

## Invariants

1. Portable recovery means portable.
2. Manifest identities must match.
3. Hidden host state is not allowed.
4. Recovery creation and restore have distinct immutable ceremony operations.

## Tests

- `tests/integration/objects/test_portable_recovery.py`

## Open questions

None.

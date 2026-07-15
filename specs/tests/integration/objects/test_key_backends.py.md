# tests/integration/objects/test_key_backends.py — key backend and recovery-domain behavior

**Wave:** C/D | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz/adapters/keys/os_keyring.md`, `src/yoetz/adapters/keys/passphrase.md`
**Imported by:** integration object/key tests

## Purpose

Prove the scripted and OS key backends behave as the contract requires for load/create/mismatch and
portable recovery.

## Public surface

- `test_create_and_load_backends` — expected keys can be created and loaded.
- `test_keyring_create_requires_first_install_authority` — pristine create requires the exact
  same-artifact keyring/presence capability token; existing load does not.
- `test_locked_missing_and_unsupported_fail_closed` — backend failures are bounded.
- `test_key_domain_mismatch_is_detected` — the wrong key domain is not accepted.
- `test_installation_mac_handles_are_opaque_and_purpose_bound` — catalog, log, and privacy-audit
  operations receive only their exact handles and domains.
- `test_installation_mac_derivation_is_stable_and_separated` — fixed IVK vectors reproduce across
  restart, distinct IVKs/installations unlink, and all three HKDF outputs differ.
- `test_bundle_hkdf_and_aes_kw_known_answers` — exact salt/info/output lengths and RFC 3394
  AES-256-KW vectors interoperate and reject tamper/wrong length.
- `test_all_object_commitment_domains_and_raw_bytes` — all 17 exact trailing-NUL domains use
  `HMAC-SHA-256(K_commit, domain || raw_plaintext_bytes)` without implicit encoding.
- `test_verified_open_rejects_missing_wrong_or_revoked_key_material` — object reads fail before
  plaintext release when the required key cannot be proven current.
- `test_passphrase_recovery_is_portable` — the portable recovery path decrypts required objects.

## Behavior

The test covers both key backend families and asserts:

- key creation/loading uses the reviewed backend contract;
- keyring `authorize_first_install` is mutation-free, rejects missing/stale/mismatched presence
  evidence, and binds its one-use authority to the probe/service/pristine-state digests;
- locked/missing/unsupported backends fail with bounded reasons;
- wrong domain or tampered key material fails closed;
- installation `K_lookup`, `K_log`, and `K_audit` are derived from the IVK with the exact ADR-004
  salt/info strings, never stored as separate vault records, and never exposed as bytes;
- bundle `K_wrap`/`K_commit` derivation uses the exact public salt, ASCII infos, and 32-byte output;
  `K_wrap` performs only RFC 3394 wrapping of exact 32-byte DEKs to exact 40-byte values;
- bundle commitment, catalog lookup, log correlation, and privacy audit all use the one opaque
  `MacKeyHandle` type but cannot substitute purposes or domains;
- every registered installation MAC-domain vector includes its literal trailing `\x00` byte and
  fails if a display string without the delimiter is used;
- every object kind, including commitment-only `import_stderr` and task-bound `privacy_audit`, has
  one enumerated domain; binary inputs prove commitments cover raw bytes unchanged;
- verified object open with missing, wrong, mismatched-ID, or revoked key material returns only the
  bounded public failure and never plaintext;
- portable backup/recovery can decrypt every required object in the clean profile.

## Errors and edge cases

- A backend that guesses a missing key fails.
- A keyring backend that creates from probe success without `FirstInstallKeyringAuthority`, or
  reuses that authority, fails before durable mutation.
- An object read that reaches plaintext before key-domain and key-ID verification fails.
- A recovery flow that silently changes the key domain fails.
- Raw-key constructor/signature exposure, independent installation-MAC records, cross-purpose MAC
  success, or a stale handle after relock fails.

## Invariants

1. Key handling is explicit.
2. Domain mismatch is fatal.
3. Portable recovery remains portable.
4. Installation MAC derivation and purpose/domain ownership match ADR-004 exactly.
5. Existing keyring load and pristine keyring creation have distinct authority requirements.

## Tests

- `tests/integration/objects/test_key_backends.py`

## Open questions

None.

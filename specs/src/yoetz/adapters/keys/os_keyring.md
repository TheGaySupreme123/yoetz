# src/yoetz/adapters/keys/os_keyring.py — installation vault-root key source

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** `ports/secret_memory.md`,
`config/paths.md`, `resources/support/runtime-support.json.md` | **Imported by:**
`service/vault.md`, keyring capability tests

## Purpose

Stores/loads the installation vault key in the approved OS keyring for the trusted service. It no
longer exposes per-bundle keys to CLI/MCP/application processes; bundle keys live as encrypted vault
records behind `VaultService`.

## Public surface

- `OSVaultRootKeySource(secret_memory: SecretMemoryPort, *, backend: object | None = None)`; the
  optional backend injection exists only for bounded adapter tests. Its exact methods are
  `async probe(installation_id) -> OSKeyringProbe`,
  `async authorize_first_install(probe, user_presence, runtime_support, *, service_generation,
  pristine_state_digest) -> FirstInstallKeyringAuthority`, `async load(installation_id) ->
  KeyringInitializationBinding`, `async create_and_verify(authority, binding, *,
  service_generation, pristine_state_digest, staged_sentinel_verifier) ->
  KeyringInitializationBinding`, and `async delete_after_proven_migration(installation_id,
  migration_verifier) -> None`.
- `enum OSKeyringState` — `available`, `locked`, `missing`, `unsupported`, `unverified`.
- `KeyringInitializationBinding` — version, installation identity, raw random correlation value and
  IVK held only in protected memory, plus the public correlation commitment used by vault staging.
- `FirstInstallKeyringAuthority` — opaque, nonserializable, one-attempt capability bound to the
  service generation, pristine-state digest, exact keyring-probe digest, candidate artifact/
  release cell, and verified `UserPresenceCapability` evidence digest. It contains no key or live
  human attestation and cannot authorize another action.
- Structural `OSKeyringProbe` and bounded `OSKeyringError`; no backend private text/path/account.
- `AutoUnlockPassphraseStore(bundle_path, *, backend=None)` with `available`, `load()`, and
  `load_or_create()`. This separate setup convenience stores one generated printable passphrase
  in an approved macOS Keychain, Windows Credential Locker, or Linux Secret Service/KWallet
  backend under a bundle-path-digest account; it never changes the strict first-install IVK
  authority above.

## Behavior

Verify exact approved macOS Keychain/Linux Secret Service backend identity before access. `load`
returns a `SecretHandle` scoped to `vault_root` plus an opaque correlation verifier; raw IVK or
correlation bytes never leave secret memory. The versioned entry payload binds installation ID,
IVK, and the one-time initialization correlation value.

The approved backend adapter must expose atomic
`set_password_if_absent(service: str, username: str, password: str) -> bool` in addition to bounded
`get_password` and `delete_password`. Ordinary overwrite-capable `set_password` is never an
equivalent. An exact approved backend without that operation is structurally `unsupported` for
first install; the adapter never emulates no-replace with a racy read-then-write sequence.

`authorize_first_install` is mutation-free. It succeeds only when a fresh probe proves the exact
approved backend's create-if-absent and round-trip-load behavior and the installed
`UserPresenceCapability` matches an active `user_presence_cells` row in the verified packaged
runtime-support manifest for the same candidate artifact/release cell. All four presence states
must be active. It returns a fresh authority bound to the current pristine-state digest and service
generation. Missing, absent, inconclusive, stale, mismatched, or cross-artifact evidence returns
bounded `human_authority_unavailable` and creates no stage, IVK, entry, or mode marker.

The caller supplies an already canonical/self-digest/resource-verified runtime-support manifest.
The adapter consumes `user_presence_cells`; the selected row must match these authorization fields:
`candidate_artifact_digest`, `release_cell`, `adapter_id`, `profile_id`,
`capability_evidence_digest`, `os_authenticated_prompt`, `trusted_action_binding`,
`one_use_attestation`, and `available`, with all four state fields `active`. Other empirical row
fields remain validated by the runtime-support resource owner and cannot widen this predicate.
`OSKeyringProbe` contains installation ID, bounded state, backend identity, create-if-absent and
round-trip booleans, and its canonical probe digest.

`KeyringInitializationBinding` is exactly version `1`, installation ID, SHA-256 correlation
commitment, one protected `vault_root_key` IVK handle, and one protected `vault_root_key`
correlation handle. `staged_sentinel_verifier(memoryview, correlation_commitment) -> None` runs on
the round-trip-loaded IVK before success; the returned binding is freshly loaded because that
verification consumes its input handle.

`create_and_verify` is first-install-only, requires and consumes that exact authority,
uses an atomic backend create-if-absent operation (or fails unsupported when the backend cannot
provide equivalent no-replace semantics), round-trip loads the exact entry, and proves its
correlation and IVK against the caller's authenticated staged sentinel before success. It never
deletes or overwrites a pre-existing entry. Only `VaultService` publishes the correlated layout/
mode after this proof. Existing-vault missing/locked never creates replacement or falls back to
passphrase. Provider credentials are stored encrypted in the vault, not separate env/keyring reads.
Existing-mode `load` does not require first-install authority: an already committed keyring vault
may unlock without current presence, while service composition separately fences external
activation.

When the user supplies `yoetz --set --api-key`, setup may generate a high-entropy passphrase,
round-trip it through `AutoUnlockPassphraseStore`, and use it to initialize the ordinary encrypted
passphrase vault. On later service starts the daemon loads that passphrase, unlocks the vault, and
immediately overwrites its mutable copy. Unsupported/unavailable platform stores fall back to the
existing explicit hidden passphrase ceremony; no plaintext file or environment fallback exists.

## Errors and edge cases

Locked/missing/unsupported/unverified remain distinct structural states. Backend prompts denied or
cancelled return locked. Unknown/plaintext backends and backends without tested create-if-absent
semantics are unsupported for first installation. An entry with a legacy/unknown envelope,
installation mismatch, or correlation mismatch is structural ambiguity/tamper; it is never
rewritten or deleted by retry. Raw backend exception text is filtered.
An authority whose service generation, pristine-state digest, probe, artifact/release cell,
presence evidence, or one-use state differs fails before keyring mutation.

## Invariants

1. Only the service vault imports/calls this adapter.
2. Key bytes never enter a client, log, config, env, receipt, trace, or SQLite row.
3. Existing key loss never triggers automatic replacement/fallback.
4. Probe success is not decrypt proof; vault sentinel verification follows.
5. First-install creation is create-once and correlation-bound; existing-mode load is never allowed
   to call it.
6. Keyring usability alone cannot mint `FirstInstallKeyringAuthority`; exact same-artifact active
   presence evidence is mandatory, and the authority is not live human approval.

## Tests

- `tests/capability/test_service_keyring_unlock.py` covers the same-artifact keyring/presence
  intersection and real disposable backend lifecycle.
- `tests/integration/service/test_locked_ready_transitions.py` covers pristine create, correlated
  resume/publication, locked/missing/load-only retry, mismatch, and restart.
- `tests/packaging/test_service_boundary_imports.py` proves client import graphs exclude keyring.

## Open questions

None.

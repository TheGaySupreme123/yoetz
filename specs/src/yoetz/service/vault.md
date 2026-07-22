# src/yoetz/service/vault.py — service-owned vault state and opaque key/credential handles

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** `ports/keys.md`,
`ports/secret_memory.md`, `adapters/keys/os_keyring.md`, `adapters/keys/encrypted_vault.md`,
`adapters/keys/vault_passphrase.md`, `service/confidential_protocol.md`,
`resources/support/runtime-support.json.md` | **Imported by:** `service/daemon.md`,
`service/unlock.md`, ready-only runtime/provider composition

## Purpose

Owns the one in-process trusted vault. It converts an OS-keyring or explicit passphrase unlock into
opaque bundle-key, installation-MAC, and provider-credential handles, enforces locked/ready
behavior, and ensures normal clients/application code cannot obtain raw IVK, BMK, derived-key,
credential, or passphrase bytes.

## Public surface

- `class VaultService` with async `initialize(user_presence_capability)`,
  `initialize_passphrase(handle, throttle_record_digest)`,
  `retry_keyring(user_presence_capability)`, `unlock`, `lock`,
  `load_bundle_keys`, `create_bundle_keys`,
  `store_provider_credential(action, binding, secret, proof, now_monotonic)`,
  `provider_credential(binding: ProviderAttemptAuthBinding)`, `wrap_recovery`,
  `mint_human_authorization`, and `close`, plus
  `installation_mac_handle(purpose: MacKeyPurpose) -> MacKeyHandle` for the three installation
  purposes registered by `ports/keys.md`.
- `enum VaultMode` — `uninitialized`, `os_keyring`, `passphrase`.
- `enum VaultState` — `locked`, `unlocking`, `ready`, `closing`, `closed`.
- `@dataclass(frozen=True, slots=True) class VaultStatus` — mode/state, format/version, bounded
  reason, secret-memory capability; no key IDs, provider names, record counts, paths, or times.
- `@dataclass(frozen=True, slots=True) class ProviderCredentialBinding` — exact structural
  `provider_id`, `model_id`, `endpoint_profile_id`, `endpoint_profile_version`,
  lower-kebab `purpose`, `authorization_scope_digest`, and `purpose_digest`; never credential
  bytes. `purpose_digest` must equal the registered canonical digest of that exact purpose.
- `provider_credential_profile_binding(...)` — the one installation-local storage binding for an
  exact provider/model/endpoint-profile tuple. It uses purpose `llm-inference` and a canonical
  profile-scope digest; per-request disclosure scope is bound later to the one-shot handle.
- `class VaultError(Exception)` with bounded reasons `keyring_locked`, `keyring_unavailable`,
  `human_authority_unavailable`, `vault_uninitialized`, `vault_locked`, `unlock_wrong`,
  `vault_tampered`, `record_missing`, `record_binding_mismatch`, `secret_purpose_mismatch`,
  `credential_invalid`, `initialization_forbidden`, `initialization_ambiguous`, `already_ready`,
  `closed`. `unlock_rate_limited` is not a `VaultError`: only `UnlockCoordinator` exposes that
  reason after consulting its throttle store.

`VaultService` implements the service-internal `KeyStorePort`. It is never serialized, exposed by
the application facade, or accepted as a control-protocol value.

The daemon, not `VaultService`, owns the immutable installation-state marker and supplies its
verified mode, optional decoded `VaultRootEnvelope`, pristine digest, and atomic `publish_mode`
callback. The canonical v1 marker format, self-digest, file-safety checks, and publication order
are frozen in `service/daemon.md`. For passphrase publication its `mode_binding_digest` must equal
the already staged/adopted initial throttle record digest; for keyring it is the initialization
correlation commitment. Existing marker, throttle, and service-generation installation identities
must agree before construction.

## Behavior

### Initialization and root key

`initialize` reads only the owner-only non-secret mode marker/authenticated envelope metadata and
routes by committed mode before any keyring mutation. Existing `os_keyring` mode performs load and
sentinel verification only; it does not require current user-presence capability and never calls
creation. Existing passphrase mode remains locked pending its confidential unlock.

A new install first proves the state is pristine and obtains a fresh keyring probe. Before creating
any stage, correlation value, IVK, entry, or mode marker, it calls
`OSVaultRootKeySource.authorize_first_install` with the installed
`UserPresenceCapability | None`, verified packaged runtime-support manifest, current service
generation, and pristine-state digest. Only the exact same-artifact active keyring/presence
intersection returns `FirstInstallKeyringAuthority`; `create_and_verify` requires that authority.
If keyring storage is usable but presence is absent/unverified, the service remains
`uninitialized/locked`, writes nothing, and reports `human_authority_unavailable` as setup
required. If the keyring is locked/unavailable, it reports that bounded keyring reason. Explicit
local-human setup may later retry both capabilities or choose passphrase mode. Existing vault mode
is immutable except through a separately previewed/reviewed migration. There is no automatic
keyring-to-passphrase fallback.

Only after `FirstInstallKeyringAuthority` exists does keyring first installation allocate one
random 256-bit initialization correlation value, represented in
normal structural state only by its SHA-256 commitment. Under singleton authority it proceeds in
this exact order:

1. Re-prove pristine state and create an owner-only authenticated empty-vault layout/sentinel in a
   same-directory staging location. Its authenticated header contains the initialization
   correlation commitment and intended mode `os_keyring`; it is fsynced and verified before keyring
   mutation.
2. Create the keyring entry once. The opaque entry payload contains the IVK plus the same raw
   correlation value under the keyring adapter's versioned envelope; `create_and_verify` performs a
   round-trip load and proves that the loaded IVK opens the staged sentinel and that the derived
   correlation commitment matches. A pre-existing entry is never overwritten.
3. Atomically publish the staged layout and immutable `os_keyring` mode marker containing the same
   correlation commitment, then fsync the parent directory. That mode-marker rename is the sole
   publication point. Startup must verify the committed marker, authenticated layout/sentinel, and
   keyring entry all correlate before becoming ready.

Crash recovery is conservative and deterministic. Before a keyring entry exists, startup may
remove/quarantine a recognizable noncommitted stage only after proving its exact staging identity,
pristine committed state, and absence of the corresponding keyring entry. If the entry was created
but the mode marker was not published, startup resumes publication only when the versioned entry,
stage, correlation, and sentinel cryptographically agree. It does not generate, delete, or replace
any key. A missing half, mismatched correlation, multiple stages/entries, unrecognized artifact, or
inability to prove whether the keyring write occurred is `initialization_ambiguous`/`vault_tampered`
and requires explicit repair outside v0.1. No automatic cleanup touches an ambiguous keyring entry.

`initialize_passphrase(handle, throttle_record_digest)` is the sole passphrase first-install vault
mutation. It requires a
one-shot `SecretHandle(purpose=vault_initialize)` and, under the service singleton, re-proves all of
the following: mode is `uninitialized`; no committed installation identity, catalog, vault sentinel/
record/ciphertext, mode marker, or partial/staging artifact exists. It allocates a fresh installation
ID as part of this ceremony. If the keyring is queryable and reports a correlated entry for that
exact new identity, initialization fails; if the backend is locked/unavailable, absence is not
pretended, but explicit passphrase initialization may proceed because the proven-pristine local
state and newly allocated identity cannot be recovery/reset of an existing installation. The
commit records the keyring probe outcome structurally. It generates a fresh 256-bit IVK in
`SecretMemoryPort`, creates the passphrase-wrapped
root envelope and authenticated empty-vault sentinel/layout in an owner-only staging location,
fsyncs/verifies them, validates and binds the coordinator-supplied digest of the already staged
generation-1 zero-failure unlock-throttle record into the authenticated layout, then commits the
immutable `passphrase` mode marker as the single publication
point and fsyncs the parent. The handle is consumed once and all intermediate buffers are
overwritten best-effort. `VaultService` never creates, reads, delays from, charges, resets, repairs,
or publishes the throttle record; `UnlockThrottleStore` and its coordinator own that complete
state machine.

Before the publication point, a clean crash may leave only recognizable incomplete staging, which
startup quarantines/removes only when its exact noncommitted identity is proven and no mode/
ciphertext was published. At or after publication, startup must prove the complete envelope,
sentinel, layout, and mode agree before using them. Any state that cannot prove one of those two
cases is `initialization_ambiguous`/`vault_tampered` and fails closed without retrying initialization,
probing a new mode, deleting bytes, or generating replacement keys. Failure of application startup
after a valid vault commit leaves a locked passphrase vault; it never reverts to uninitialized.
Once `passphrase` mode commits, startup never probes or uses keyring as fallback. A later-discovered
foreign/stale keyring entry is ignored and never deleted or treated as authority; any exact
identity-correlation contradiction found by explicit maintenance is quarantined for reviewed repair.

`retry_keyring` accepts no secret and has exactly two state-fenced branches. For a pristine
`uninitialized` install it re-probes both the approved keyring and installed user-presence release
cell. It runs the staged create-once protocol only after minting a fresh exact
`FirstInstallKeyringAuthority`; keyring failure or `human_authority_unavailable` writes nothing and
stays uninitialized/locked. For committed `os_keyring` mode it loads only the correlated existing entry,
verifies the sentinel, and never calls creation. Passphrase mode, ambiguous staging, a non-pristine
uninitialized state, or a committed keyring marker with a missing/mismatched entry rejects the
retry without mutation. In passphrase mode `unlock` accepts one `SecretHandle` with purpose
`vault_unlock` only after `UnlockCoordinator` has passed and durably reserved the throttle gate;
it never reads or mutates throttle state. It unwraps
the IVK through `vault_passphrase`, validates the encrypted vault sentinel, and becomes ready. The
passphrase is consumed once and never retained. Before publishing `ready`, either route derives
the exact installation `K_lookup`, `K_log`, and `K_audit` family from the IVK using the ADR-004
HKDF salt/info registry and mints purpose/domain/generation-bound `MacKeyHandle` values. Failure to
derive or bind the complete family leaves the service locked; partial handles are invalidated.

### Vault records and handles

The encrypted vault stores authenticated records under the IVK for:

- one random BMK per bundle plus structural key slot/algorithm/version binding;
- provider credentials bound to one exact provider/model/endpoint profile storage scope;
- optional recovery metadata containing no recovery secret.

It stores no independent `K_lookup`, log-correlation, or privacy-audit key record. Those three keys
exist only as unlock-time derivations from the stable IVK inside protected service memory.
Each immutable record generation uses a fresh one-use AES-256-GCM record DEK; the IVK wraps only
that exact 32-byte DEK with nonce-free AES-256-KW (RFC 3394). The vault also derives the private
`K_vault_locator` using ADR-004's exact HKDF parameters for record locator/index authentication;
that internal key never becomes a record or crosses the vault adapter boundary. The exact YZV1
frame, no-overwrite generation-CAS index, and crash recovery are owned by
`adapters/keys/encrypted_vault.md`.

`load/create_bundle_keys` return only opaque `BundleKeys` operations. BMKs and derived keys stay
inside the vault/object crypto boundary. `create` is once per bundle and fails on an existing or
contradictory record. `installation_mac_handle` accepts only `catalog_lookup`, `log_correlation`, or
`privacy_audit` and returns the already-bound opaque handle; requesting `bundle_commitment` or
calling a handle with another purpose's domain fails closed. The service composition injects only
the catalog handle into start-catalog adapters, only the log handle into observability privacy, and
only the audit handle into the privacy gateway/audit path. The `privacy_audit` handle admits only
the closed audit domains used by catalog privacy persistence: egress request, lookup, proposal,
control request, internal result, projection, local approval, and receipt cursor. For each physical outbound attempt,
`provider_credential(binding)` returns a fresh one-use `ProviderCredentialHandle` restricted to the
exact provider/model/endpoint-profile/version, purpose plus authorization-scope/purpose digests,
dispatch ID, final request-body digest, service generation, and deadline in
`ProviderAttemptAuthBinding`. Provider/model/profile fields select the stored profile credential;
the attempt's scope/purpose/body/dispatch/generation/deadline are independently bound to the fresh
handle before minting. Backward read may fall back to the older exact-scope development record. It
can expose a protected view
only inside the custom transport's header-injection callback, cannot reveal/reuse bytes or
authorize another body/destination/attempt, and is invalid after that callback. A retry mints a new
dispatch-bound handle. `store_provider_credential` accepts exact
`action: Literal["set", "rotate"]`, a confidential-ingress
`SecretHandle(purpose=provider_credential)`, the frozen `ProviderCredentialBinding`, the unexported
`HumanAuthorizationProof`, and explicit monotonic time. It requires the proof purpose
`provider_credential_set` or `provider_credential_rotate` to match `action`, and validates the
proof's exact target digest, service/vault generations, absent policy generation, expiry, and
one-use state against the same ceremony/binding. It
resolves the exact installed provider-profile validator from the closed bundled registry and runs
that validator inside protected one-shot consumption before encryption. For the OpenAI profile this
is `validate_openai_credential`'s 16..512-byte token68 rule. Invalid input yields only
`credential_invalid`, consumes/overwrites the ingress handle, and writes no vault record, staging
artifact, adapter state, length, offset, or diagnostic text; the vault never trims/normalizes it.
After secret validation and storage preflight, the vault enters one non-interleavable mutation
section, consumes that exact proof, rechecks the target/generations, and performs the set-or-rotate
record-generation CAS. No record mutation can commit without proof consumption. Any failure after
consumption leaves the proof spent and preserves the prior committed record; retry requires a new
ceremony, proof, and credential handle.
`set` creates a missing exact profile credential or replaces its current authenticated index
generation; repeating `yoetz --set` therefore updates the key. `rotate` remains existing-only.

`mint_human_authorization(source, challenge)` is the sole constructor of
`HumanAuthorizationProof`. It accepts exactly one matching internal authorization source: an
unconsumed `UserPresenceAttestation` from the capability-tested `UserPresencePort`, or a
`provider_reauthentication`, `privacy_reauthentication`, or `security_reauthentication` secret
handle in committed passphrase mode. For the attestation branch the vault invokes
`UserPresencePort.consume(attestation, challenge)`, which returns no authority, then mints the
proof itself. For a secret branch it
re-runs the immutable envelope's exact Argon2id parameters, unwraps a candidate IVK into protected
memory, verifies the candidate against the authenticated vault sentinel and current installation-
key derivation domain, and constant-time compares only fixed commitments—not raw key/passphrase
bytes. `UnlockCoordinator` must durably reserve the attempt before this method is called and is the
only component that charges/resets the shared throttle after its bounded outcome; the vault does
neither. Wrong input/tamper has one bounded outcome and consumes the candidate. Keyring mode has no
secret branch. The method binds the proof to the challenge's exact authorization purpose and target
digest, ceremony/service/vault/optional-policy generations, and
`confidential_protocol.CEREMONY_EXPIRY_SECONDS`; it never turns a CLI/TTY
boolean or same-UID assertion into authorization.

For credential set/rotate, the same method consumes either an exact presence attestation or the
distinct `provider_reauthentication` purpose in passphrase mode, verifies it through that same
envelope/sentinel algorithm, and binds an internal one-use proof to the exact credential ceremony and
`ProviderCredentialBinding`. That proof can authorize only the subsequent atomic credential write;
it cannot approve privacy, unlock, or be returned to the helper.

For a current-generation idle-relock change, it accepts only an exact presence attestation or the
distinct `security_reauthentication` purpose and binds the proof to the exact current/proposed
policy target digest. It cannot authorize provider or privacy work, and neither of those purposes
can authorize disabling idle relock.

### Relock

`lock` stops new handles, waits for admitted secret consumers to exit under lifecycle control,
invalidates every outstanding bundle, installation-MAC, and credential handle, closes encrypted
records, overwrites mutable buffers best-effort, and returns locked. Handles check vault generation
on every operation. Session wake,
client reconnection, config reload, or provider retry cannot reopen it.

## Errors and edge cases

- A wrong passphrase and tampered envelope are externally indistinguishable beyond bounded local
  handling needed for repair; neither leaks proximity or record existence.
- Keyring entry missing for an existing OS-backed vault is not first-install creation and never
  generates a replacement. The same is true for an entry/layout/correlation mismatch.
- A usable pristine keyring with absent/unverified/mismatched presence capability creates no
  stage, correlation, IVK, entry, or marker and returns `human_authority_unavailable`; an explicit
  passphrase ceremony remains a separate local-human choice.
- A keyring entry appearing during passphrase setup, an existing/partial ciphertext layout, a
  changed first-install-state digest, or an already selected mode makes passphrase initialization
  forbidden/ambiguous; it is never treated as an empty install.
- Crash during first initialization leaves pristine state, an exact safely removable pre-entry
  stage, an exact resumable correlated entry+stage, or a fully committed correlated keyring vault.
  Anything else is ambiguous/tampered and is neither overwritten nor auto-deleted.
- Provider credential absence causes capability unavailable/incomplete semantic behavior, never an
  environment/config fallback.
- Provider credential format validation is exact-profile, offline, and pre-persistence; invalid
  bytes never become a partially published record or a provider probe.
- A stale handle after lock/restart/service-generation change fails closed.
- An absent or wrong-purpose installation MAC handle never falls back to bytes, a static key, an
  unkeyed digest, or another purpose's handle.

## Invariants

1. Only ready `VaultService` can produce bundle/installation-MAC/provider handles; clients never
   receive one.
2. Root/bundle keys and credentials are plaintext only inside bounded service secret-memory use.
3. Vault mode never changes silently and key loss never creates replacement keys over ciphertext.
4. Provider credentials are least-authority bound, not generic secret strings.
5. Locked is not empty data and does not admit deterministic workflow reads of encrypted payloads.
6. `vault_initialize` is accepted only for a pristine installation; existing OS-keyring or
   passphrase state can never use it as fallback, unlock, reset, or recovery.
7. The sole installation `K_lookup`, `K_log`, and `K_audit` family derives from the IVK, is never
   stored independently, and crosses module boundaries only as purpose-scoped `MacKeyHandle`.
8. A keyring vault becomes committed only when entry, authenticated sentinel/layout, immutable mode
   marker, and initialization correlation agree; retry never converts missing-key ambiguity into a
   new key.
9. Human authorization is minted only from exact OS-backed presence or purpose-specific
   confidential reauthentication; TTY acknowledgement and caller booleans are never sources.
10. A keyring vault without measured user presence cannot mint durable-widening proof or mutate
    provider credentials; no hidden passphrase enrollment or keyring-as-human-proof path exists.
11. Passphrase mode publication binds an initialized throttle record; missing/corrupt throttle
    state later never becomes an unthrottled verification path.
12. Pristine automatic keyring mode is impossible without a fresh exact
    `FirstInstallKeyringAuthority`; existing keyring-mode load remains allowed without current
    presence and is externally fenced by ready composition.
13. `UnlockCoordinator` exclusively owns throttle admission and durable throttle transitions;
    `VaultService` performs a requested cryptographic verification only after that gate and is the
    sole minter of every `HumanAuthorizationProof`.

## Tests

- `tests/unit/service/test_vault_state.py` covers the complete mode/state/handle-generation matrix.
- `tests/integration/service/test_locked_ready_transitions.py` covers first setup, both keyring
  retry branches, the keyring/presence capability cross-product and no-write rejection, every
  keyring publication crash boundary, passphrase unlock, relock, crash/
  restart, and stale handles.
- `tests/integration/objects/test_key_backends.py` covers bundle, installation-MAC, and provider
  credential bindings through the ready vault, including wrong-purpose/domain rejection.
- `tests/integration/service/test_encrypted_vault.py` covers exact-profile credential accept/reject
  vectors, no-write failure, exact-byte round trip through the one-attempt callback, and no logs.

## Open questions

None.

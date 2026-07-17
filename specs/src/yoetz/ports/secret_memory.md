# src/yoetz/ports/secret_memory.py — swappable protected-memory and secret-handle boundary

**Wave:** B | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):**
`domain/values.md`, `protocol/ids.md` | **Imported by:**
`service/vault.md`, `service/secret_ingress.md`, `service/human_control.md`, key/provider adapters

## Purpose

Defines how the trusted service contains short-lived key, credential, passphrase, and decrypted-
secret bytes without pretending Python guarantees zero-copy or perfect zeroization. The boundary
is replaceable by a stronger native vault implementation without changing CLI, MCP, workflow, or
provider-policy contracts.

## Public surface

- `enum SecretPurpose` — `vault_initialize`, `vault_unlock`, `portable_recovery`,
  `provider_reauthentication`, `provider_credential`, `privacy_reauthentication`,
  `security_reauthentication`. Initialization, later unlock, credential reauthentication,
  credential bytes, privacy reauthentication, and security-policy reauthentication are distinct
  purposes and handles are never interchangeable.
- `@dataclass(frozen=True, slots=True) class SecretMemoryCapability` — positive evidence for
  bounded mutable allocation, page locking, core-dump suppression, one-shot consumption, and
  best-effort overwrite; each field is `supported|active|unavailable`, never a claim inferred from
  platform name.
- `class SecretMemoryPort(Protocol)` with `capability()`, `capture(purpose, source: bytearray) ->
  SecretHandle`, `allocate(purpose, size) -> SecretHandle`, and `close()`.
- `class SecretHandle(Protocol)` — nonserializable, noncopyable, constant-redacted representation;
  `consume(consumer: SecretConsumer, fn)` exposes one bounded writable `memoryview` to an
  allowlisted service-internal consumer exactly once and overwrites/releases it in `finally`.
- `enum SecretConsumer` — `vault_root`, `recovery_wrapper`, `provider_authorizer`,
  `privacy_authorizer`, `security_authorizer`.
- `@dataclass(frozen=True, slots=True) class ProviderAttemptAuthBinding` — exact `provider_id`,
  `model_id`, `endpoint_profile_id`, `endpoint_profile_version`, `purpose`,
  `authorization_scope_digest`, `purpose_digest`, `dispatch_id`, `request_body_digest`, service
  generation, and absolute/monotonic deadline. It is internal, nonsecret, and contains no
  URL/header/credential/body.
- `class ProviderAuthTransportCallback(Protocol[T])` — custom HTTP-transport-only
  `async inject_and_start(credential_view: memoryview) -> T`. It may use the view only to inject the
  exact bound authentication header and start that one request; it cannot alter the request body,
  destination, or deadline and cannot retain the view.
- `class ProviderCredentialHandle(Protocol)` — opaque single-physical-attempt handle with
  `async authorize_attempt(binding: ProviderAttemptAuthBinding,
  inject_and_start: ProviderAuthTransportCallback[T]) -> T`. It checks its fixed vault binding,
  body digest, dispatch, deadline, and service generation, invokes the custom transport callback
  once inside protected-memory consumption, and never returns reusable credential bytes.
- `@dataclass(frozen=True, slots=True) class HumanAuthorizationProof` — non-secret proof ID,
  with exact public fields `proof_id`, `purpose`, `target_digest`, `service_generation`,
  `vault_generation`, `policy_generation: int | None`, `issued_at_monotonic: float`, and
  `expires_at_monotonic: float`. Its private `_consume_latch` field is an internal mutable
  one-shot latch declared `init=False, repr=False, compare=False` and excluded from copy, pickle,
  and serialization; it is not a public constructor value. The service-internal
  `consume(expected_purpose, expected_target_digest, service_generation, vault_generation,
  policy_generation, now_monotonic) -> None` validates every binding/expiry field and flips that
  latch exactly once or raises a bounded mismatch/expired/already-consumed error. `VaultService` is
  the sole constructor/minting authority; an exact approved OS-backed presence attestation or
  purpose-specific confidential reauthentication is its required input. The proof is immediately
  consumed with the exact pending change and never serialized or returned to a helper/client.
- `@dataclass(frozen=True, slots=True) class UserPresenceChallenge` — exact authorization purpose,
  ceremony/target/display-summary digests, service/vault generation, optional policy generation,
  and monotonic expiry;
  constructed only from a live human-control ceremony.
- `class UserPresenceAttestation(Protocol)` — opaque nonserializable, nonconstructible outside the
  installed adapter, one-use result bound to every challenge field; never a generic boolean or
  reusable credential.
- `@dataclass(frozen=True, slots=True) class UserPresenceCapability` — exact adapter/profile/
  platform identity and measured `os_authenticated_prompt`, `trusted_action_binding`,
  `one_use_attestation`, and `available` states plus capability-evidence digest and candidate-
  artifact digest. Each measured state is `active|unavailable`; no inferred `supported` value
  authorizes runtime behavior.
- `class UserPresencePort(Protocol)` with `capability() -> UserPresenceCapability`,
  `async assert_presence(challenge: UserPresenceChallenge) -> UserPresenceAttestation`,
  `consume(attestation, challenge) -> None`, and `close()`. `consume` validates and invalidates the
  exact opaque attestation or raises; it does not mint or return authority. `VaultService` invokes
  it while minting the common proof.
- `class SecretMemoryError(Exception)` with bounded reasons `size_invalid`, `purpose_mismatch`,
  `consumer_forbidden`, `already_consumed`, `memory_lock_failed`, `closed`,
  `provider_binding_mismatch`, `provider_body_digest_mismatch`, `provider_deadline_expired`,
  `provider_transport_forbidden`, `presence_capability_unverified`, `internal_error`.

## Behavior

All input is copied once from an already mutable, bounded source into the protected allocation;
the source is overwritten immediately. The implementation attempts OS page locking and disables
core dumps where positively supported and tested. A failed optional page lock is recorded as
`unavailable` and does not become a false security claim; a profile that requires page locking
fails closed through an explicit capability gate.

`consume` checks purpose/consumer, exposes a writable view only during the callback, and overwrites
the full allocation before release even when the callback raises or is cancelled. It never returns
the view or immutable bytes. This limits avoidable copies, but cryptography, HTTP, keyring, and
Python internals may copy data outside the port's control; specs and UI must state this honestly.

Provider credentials are stored encrypted in the service vault and materialized only as
one fresh `ProviderCredentialHandle` per physical attempt. After deterministic adapter rendering,
the gateway binds the handle to the exact provider/model/endpoint profile/version, purpose,
authorization-scope digest, purpose digest, dispatch ID, SHA-256 digest of the final request body,
current service generation, and deadline.
The handle cannot be reused for retry; a retry needs a new dispatch and handle.

Only the injected custom HTTP transport callback can transiently receive the protected
`memoryview`. It verifies that the actual body digest and endpoint/profile equal the binding,
replaces only the credential-bearing authentication header, starts the one request, and returns
immediately after header injection/request start so the source view is overwritten/released in
`finally`. Credential bytes never enter the provider SDK client, its default-header storage,
`OpenAIProfile`, CLI, MCP, config, environment, application events, approved case, logs, or
receipts. Python/HTTP/TLS internals may copy the injected header, so the contract is bounded lifetime
and no reusable application/SDK copy—not perfect zero-copy.

`UserPresencePort` is a strong local-human authorization boundary, not a convenience prompt. An
installed implementation uses an approved OS authentication facility that identifies the Yoetz
service, binds the reviewed action summary, requires fresh local interaction, and returns an opaque
process-internal attestation for the exact challenge. TTY availability, keypress/readline input, a
confirmation flag, same-UID peer identity, unlocked-screen state, an agent response, and a desktop
notification are never implementations of this port. Capability is measured and exact-profile
gated; platform-name inference is forbidden.

Pristine automatic keyring initialization uses the capability as release evidence, not as an
action attestation. Eligibility is exact and conjunctive: the capability is non-null; its adapter,
profile, normalized platform/release-cell, and candidate-artifact identities exactly equal one
`user_presence_cells` row in the packaged runtime-support allowlist; its evidence digest equals
that row; and `available`, `os_authenticated_prompt`, `trusted_action_binding`, and
`one_use_attestation` are all `active`. Any absent, unavailable, inconclusive, stale, mismatched,
unknown, or cross-artifact value is `presence_capability_unverified`. The trusted service evaluates
this predicate from the installed port and packaged manifest; it never accepts an eligibility
boolean or capability object from CLI, MCP, configuration, environment, an agent, or an LLM.

Passing this predicate permits only the pristine create-once keyring-mode initialization branch.
It is not `UserPresenceAttestation`, does not prove that a human approved a current action, and
cannot authorize policy widening, provider credential mutation, disclosure, recovery, or any later
ceremony. Those actions still require a fresh exact challenge-bound attestation.

For a durable authority or security-policy change, if the port is absent, unavailable, cancelled,
or cannot bind the exact action, human control does not downgrade to TTY acknowledgement. A
committed passphrase vault may instead complete purpose-specific YZS1 reauthentication. A keyring
vault has no passphrase alternative and fails closed/local-only; it never turns keyring access into
human proof or enrolls a secret implicitly. Denial/cancel remains secret-free.
`confirm_every_request` consent for one exact already-policy-authorized case is not a durable
authority change and uses digest-bound foreground TTY approve/deny without minting this proof.
Only `VaultService` invokes exact attestation consumption or consumes a matching
purpose-specific reauthentication handle and mints the common one-use proof. The presence adapter,
`UnlockCoordinator`, and `HumanControlService` never construct a proof.

## Errors and edge cases

- `repr`, `str`, dataclass conversion, pickle, deepcopy, equality-by-content, hashing-by-content,
  and exception text never reveal length or bytes.
- Fork after vault readiness is forbidden; child inheritance of protected mappings/handles is a
  fatal service defect.
- Swap/page-lock behavior differs by platform and resource limit. Capability reporting is measured
  at service startup, not assumed.
- A callback retaining a view is a defect; the adapter invalidates/releases it and tests exercise
  deliberate retention attempts.
- Binding/body/deadline mismatch fails before credential exposure or socket I/O. A stock SDK
  transport, reusable client credential, generic callback, redirect, or retry cannot consume the
  handle.
- User-presence timeout/cancel/unavailable/binding mismatch returns a bounded failure and mints no
  proof. It never falls back internally or returns an `approved` boolean.
- A missing or nonmatching runtime-support row fails pristine keyring eligibility before any vault
  stage, IVK, keyring entry, or mode-marker mutation. Existing keyring-mode load is not routed
  through that creation gate.

## Invariants

1. Secret bytes exist only inside the confidential helper briefly and the ready service's secret-
   memory boundary; never in an ordinary client.
2. Every handle has one purpose, allowlisted consumers, bounded size, and at most one consumption.
3. No API returns raw secret `str` or immutable `bytes`.
4. Page-lock and overwrite behavior is reported as best effort, never perfect zeroization.
5. A future native vault subprocess can implement this port without changing higher layers.
6. `vault_initialize` is consumable only by `vault_root` during the uninitialized-empty
   transition; an existing passphrase/keyring vault can consume only its established unlock path.
7. Provider reauthentication can supply one credential-ceremony proof input but cannot carry/store
   a credential; `provider_credential` cannot reauthenticate or authorize another binding.
8. Every physical provider attempt consumes one endpoint/profile/body-digest/deadline-bound
   credential handle through the custom transport callback; no SDK object retains the real key.
9. TTY interaction is not user-presence evidence. Widening authorization requires either a
   measured OS-backed exact attestation or distinct confidential reauthentication.
10. Exact foreground TTY consent may approve one confirm-every-request case already inside durable
    policy; that consent never becomes `HumanAuthorizationProof` or policy authority.
11. Automatic pristine keyring creation requires exact artifact-bound active presence capability;
    capability evidence is neither a live attestation nor authority for any other operation.
12. `privacy_reauthentication` and `security_reauthentication` are disjoint; disabling idle relock
    cannot consume privacy authority, and neither proof can be rebound to another target.

## Tests

- `tests/unit/service/test_secret_memory.py` covers all seven purpose/consumer pairs, rejects
  initialization/unlock substitution, and covers size/one-shot semantics,
  redacted representations, cancellation, callback retention, overwrite fault injection, exact
  provider-attempt binding, body/deadline mismatch, custom-transport identity, and retry reuse.
- `tests/capability/test_service_keyring_unlock.py` records measured page-lock/no-core-dump
  capability on each advertised platform.
- `tests/capability/test_user_presence.py` records exact OS-backed prompt/action-binding/
  cancellation/replay evidence and the artifact-bound first-install eligibility row; unsupported
  cells require explicit passphrase setup or remain uninitialized, and existing keyring cells use
  ready-local fencing.
- `tests/packaging/test_private_boundary_and_secret_scan.py` canary-scans process arguments,
  environment, logs, traces, frames, dumps, and temporary files.

## Open questions

None.

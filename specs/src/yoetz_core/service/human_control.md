# src/yoetz_core/service/human_control.py — foreground multi-phase confidential human control

**Wave:** D | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):**
`service/confidential_protocol.md`, `ports/privacy.md`, `ports/secret_memory.md`
(`UserPresencePort`), `service/secret_ingress.md`, `service/unlock.md`, `service/vault.md`,
`application/maintenance.py.md` | **Imported by:** `service/daemon.md`

## Purpose

Own the one reachable trusted local-human coordination path for vault initialization/unlock,
zero-secret keyring retry, confirmed portable recovery, provider credential provision/rotation,
policy widening, and individual disclosure decisions. Ordinary/MCP control cannot create a
challenge, receive a sensitive preview, submit a decision, or reach secret ingress.

## Public surface

- `class HumanControlService` with async `open_ceremony`, `submit_action`, `secret_completed`,
  `cancel`, and `close`; it delegates vault work to `UnlockCoordinator`, maintenance binding to
  `MaintenanceService`, credential writes to `VaultService`, and privacy decisions to the owning
  application use cases.
- Bounded `HumanControlError`; no method returns a proof/token/secret/generic authority.
- Constructor dependency `UserPresencePort | None`; a capability-tested OS-backed implementation
  is optional, but TTY acknowledgement can never fill this dependency.

## Behavior

The daemon binds the third owner-only same-UID endpoint through `unix_socket.md`. It parses and emits
only the exact YZH1 envelopes owned by `confidential_protocol.md`; this server module owns no second
wire model and exports no client. The service validates state/authority before returning the exact
ceremony binding/preview/phase. Preview is rendered only by a trusted client on `/dev/tty`; it never
enters ordinary/MCP frames, logs, traces, or agent context. Unknown kind/field is fatal.

Next phases are exact:

- `keyring_retry`: after the helper locally acknowledges the structural retry preview, a typed
  `{action:"retry"}` frame invokes `UnlockCoordinator.retry_keyring` with zero secret. A pristine
  retry remeasures both the keyring cell and exact installed `UserPresencePort` release cell before
  any write; an existing keyring retry is load-only. It returns only ready/locked plus bounded
  reason, including `human_authority_unavailable`; the secret protocol still rejects zero-length
  frames.
- `vault_initialize|vault_unlock|portable_recovery`: the service returns a
  `SecretIngressBinding`; the helper performs its no-echo ceremony and sends one YZS1 secret frame
  on the separate one-secret endpoint. The human-control session waits for the internal consumed
  outcome and returns structural state only. Recovery is openable only after exact plan confirmation.
- `provider_credential_set|provider_credential_rotate`: the preview shows exact provider/endpoint/
  scope/purpose and set-vs-rotate. The service first requires either a capability-tested
  `UserPresencePort` attestation bound to this exact preview/ceremony or a separate
  `provider_reauthentication` YZS1 secret; the common proof remains internal. It
  then mints a `provider_credential` binding, accepts one credential secret, and atomically stores
  or replaces the encrypted record. Failed rotation preserves the old record. Success calls
  gateway `reconcile_policy` with the current `HumanAuthorityCapability` and returns only action,
  binding digest, and activation status.
- `privacy_policy_decision` loads the exact persisted transition, freezes its diff/generations, and
  returns the bounded preview. Expansion approval requires either the measured `UserPresencePort`
  or an established passphrase-mode `privacy_reauthentication` secret; deny/edit needs no secret
  but stays exact-digest bound. The service consumes the proof atomically with commit.
- `privacy_disclosure_decision` is only for an exact prepared case already inside every durable
  effective-policy category/provider/purpose/scope/size ceiling and marked
  `authorization_change=none`. In `confirm_every_request`, foreground `/dev/tty` preview plus typed
  digest-bound `approve|deny|edit` is the required consent and does not invoke OS presence or
  passphrase reauthentication. A case outside durable policy is rejected here and must become a
  separately reauthenticated policy transition; this decision can never widen policy.

Each connection owns at most one ceremony. Binding expiry is 60 seconds and every action/secret
handoff rechecks peer UID, service/vault/policy generation, target digest, phase, and one-use state.
Disconnect/cancel consumes all pending bindings and overwrites staged secrets. The human endpoint
contains structural previews/actions only; secret bytes remain exclusively YZS1. TTY is an
explicit ceremony, not cryptographic proof against malicious same-UID automation.

Strong reauthentication has two and only two realizations. A passing `UserPresencePort` attestation
works in either ready vault mode. The secret branch exists only for committed `passphrase` mode and
verifies the entered bytes against the existing immutable vault-root envelope and sentinel; keyring
material itself is never treated as a human secret. Therefore an `os_keyring` vault without a
verified presence capability cannot set/rotate provider credentials, widen privacy policy, or
weaken idle/security policy. It stays external-egress-disabled/local-only and returns
`reauthentication_unavailable`; it does not invent an admin passphrase, use TTY acknowledgement,
delete/recreate the vault, or ask an MCP/agent. Deterministic and locally permitted work, policy
tightening, status, lock, and confirm-every-request decisions within already authorized durable
policy remain structurally available as applicable.

That ready-local rule is only for an already committed keyring vault. On pristine
`uninitialized` state, missing or unverified presence capability prevents automatic keyring mode
before any mutation. The `vault_initialize` preview then names `human_authority_unavailable` as the
setup reason and offers explicit passphrase initialization as a separate irreversible human
choice; it never automatically advances from retry into secret collection.

## Errors and edge cases

- Stale generation, changed excerpt/destination/diff/binding, expiry, disconnect, relock, or replay
  consumes the ceremony and commits nothing.
- Credential set rejects an existing record; rotate requires one and performs atomic replace.
  Provider profile/policy mismatch or missing reauthentication creates no credential binding.
- Keyring retry is available only for a re-proven pristine `uninitialized` re-probe/create attempt
  or committed `os_keyring` load attempt. It cannot create for an existing vault, select
  passphrase fallback, repair ambiguity, delete/replace a key, or accept secret bytes.
- Pristine retry with usable keyring but nonpassing user-presence evidence returns
  `human_authority_unavailable` and creates no keyring/vault artifact; explicit passphrase setup
  requires a new `vault_initialize` ceremony.
- Ordinary CLI/control/MCP schemas and agent/LLM messages cannot represent or authorize a ceremony.
  A malicious same-UID process may raw-connect/emulate TTY and remains an explicitly documented
  threat-model limitation; YZH1 peer/TTY checks are not claimed as cryptographic human exclusion.
- A TTY keypress, same-UID connection, unlocked session, or unbound OS prompt cannot be promoted to
  `HumanAuthorizationProof`; failed/unavailable presence requires exact secret reauthentication.
- In committed `os_keyring` mode that secret alternative does not exist. Missing strong presence
  makes durable authority expansion/provider mutation unavailable and fences external adapters;
  there is no fallback secret enrollment in v0.1.
- Conversely, requiring strong reauthentication for a `confirm_every_request` prepared disclosure
  already within durable policy is a defect; exact foreground TTY consent is its authority.
- Human-control framing rejects duplicate/unknown fields, floats, oversize, out-of-order phase,
  YZS1 bytes on YZH1, and typed decisions for the wrong ceremony.

## Invariants

1. Widening/disclosure approval commits only the exact previewed digest.
2. Reauthentication proof never leaves service and is atomically single-use.
3. Every confidential challenge is created through this endpoint; helpers never invent one or
   receive one through ordinary control.
4. Zero-secret keyring retry, secret-bearing YZS1 phases, and typed privacy decisions are disjoint.
5. Ordinary CLI and MCP import graphs cannot import `service/confidential_client.md` or receive a
   sensitive preview; the explicitly trusted CLI helper imports the client-safe module but never
   this server module.
6. Tightening remains immediate through ordinary control.
7. Every durable-widening proof has one of two explicit sources: exact OS-backed user presence or
   established passphrase-mode confidential reauthentication; there is no TTY/keyring fallback.
8. Confirm-every-request TTY consent is sufficient only for one exact already-authorized prepared
   disclosure and cannot change durable authority.
9. Zero-secret pristine keyring retry cannot cross the two-capability gate or turn its failure into
   implicit passphrase setup; existing-keyring load and pristine creation remain distinct.

## Tests

- `tests/integration/service/test_human_control.py` covers every ceremony, phase/binding race,
  approve/deny/edit/proof use, pristine keyring/presence retry and explicit setup choice,
  existing-keyring load retry, credential set/rotate, and reconciliation.
- `tests/subprocess/test_privacy_human_control.py` covers TTY-only preview and forbidden surfaces.
- `tests/integration/service/test_secret_ingress.py` covers every ceremony-to-secret binding
  handoff and zero-secret separation.
- `tests/capability/test_user_presence.py` gates each advertised OS-backed implementation;
  integration tests inject both passing/failing ports and prove secret fallback is explicit.
- `tests/packaging/test_service_boundary_imports.py` proves confidential clients import the pure
  protocol but not this server, and ordinary/MCP graphs import neither.

## Open questions

None.

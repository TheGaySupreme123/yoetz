# docs/protocol/local-service-security.md — local service, vault, and confidential-input security

**Wave:** E | **ADRs:** ADR-001, ADR-004, ADR-008 | **Imports (spec-tree):**
`src/yoetz/service/lifecycle.md`, `service/control_protocol.md`, `service/vault.md`,
`service/secret_ingress.md`, `cli/unlock.md` | **Imported by:** public README/privacy/security docs

## Purpose

Publicly explains what the persistent local service protects, what locked/ready mean, which clients
are untrusted surfaces, how a human unlocks safely, and the limits of same-UID/process-memory
protection. It must be understandable without private architecture files.

## Public surface

The document contains: trust-boundary table; foreground service run/status/lock/unlock lifecycle;
locked/ready behavior; CLI/MCP/UI client rules; ordinary, one-secret, and multi-phase human-control
local channels;
forbidden secret surfaces; first-install keyring/passphrase initialization, later unlock, and
recovery distinction; idle/session/suspend relock;
same-UID/root/live-memory limitations; headless and native-vault status; troubleshooting reason
codes; links to ADR-001/004/008, privacy protocol, key-recovery runbook, and security policy.

## Behavior

State the binding user promise plainly: one local service owns writers, keys, credentials, and
decrypted state; ordinary clients never do. On keyring-backed or pristine keyring-eligible paths,
keyring success unlocks once and expected failure leaves a reachable locked service. A committed
passphrase-backed vault does not probe keyring and remains locked pending confidential unlock.
TTY-only confidential input has no stdin/argv/env/config/MCP fallback.
Wake does not unlock. Explain 15-minute quiescent default, explicit/session/suspend relock, and
durable retry after drain/connection loss.

Describe the three fixed owner-only same-UID endpoints: ordinary YZ control; YZS1 one-secret
ingress, which never creates a challenge; and YZH1 human control, which alone creates closed
ceremonies/previews/bindings, performs zero-secret keyring retry, coordinates provider credential
set/rotate, and accepts typed privacy decisions. Ordinary MCP/control schemas and import graphs
expose no connector for the two human paths; arbitrary malicious same-UID code can still emulate a
raw client and is an explicit threat-model limitation. Provider provisioning requires exact binding
reauthentication, atomic set/rotate, and post-store policy reconciliation; secrets never enter
normal command values.

Explain the pristine first-install fork explicitly. Automatic keyring mode requires both a usable
verified create/load keyring cell and an artifact-verified action-bound `UserPresencePort` cell for
the exact installed release artifact. If keyring storage works but presence evidence is absent,
unavailable, inconclusive, stale, or mismatched, the service creates no stage, IVK, keyring entry,
or mode marker and remains `uninitialized/locked` with `human_authority_unavailable` setup-required
status. If the keyring itself is unavailable/unusable, it remains uninitialized/locked with the
bounded keyring reason. Neither branch falls back.
Only an explicit foreground `service initialize-passphrase` ceremony may select passphrase mode.
The helper confirms twice locally but transmits one `vault_initialize` secret; the service accepts
it only with proven absence of a committed installation identity/catalog/mode, ciphertext, sentinel,
or ambiguous staging, and allocates a fresh installation ID. Queryable exact-correlated keyring
state blocks; locked/unavailable keyring state is recorded but does not block the explicit pristine
choice. Later unlock uses the distinct `vault_unlock` purpose. Existing keyring/passphrase state can never use
initialization as reset/recovery, and crash ambiguity fails closed without deleting data.
Passphrase-mode startup never probes or falls back to keyring; foreign/stale entries are ignored.

Explain the F-010 distinction between new and existing vaults. A new immutable keyring mode is not
created without the verified presence cell. An already committed keyring vault may still unlock
without current presence so local data remains usable, but it is ready-local only: external
activation, provider-credential mutation, privacy widening, and other durable authority changes
fail closed. TTY acknowledgement and same-UID identity are not fallbacks. F-010 continues to cover
additional platform presence adapters, a separately designed admin-authorization secret, and
reviewed migration behavior.

State that native service-manager install/start integration is deferred and an external per-user
supervisor may run the foreground command without receiving secrets. Show allowed/blocked examples
without sample real secrets. Explain that same-UID authentication and
owner-only sockets block other local users but not a compromised active account/root/live process;
page locking/overwrite are best effort, not perfect zeroization. State that v0.1 has no passphrase
headless FD and uses an in-process swappable vault rather than claiming native isolation.

Explain that ordinary exception handling retains only bounded correlation/reason identity. v0.1
does not capture raw traceback content in plaintext or encrypted form, even to an owner-only file;
any future encrypted diagnostic artifact requires a separate reviewed privacy-authorized feature.
Also explain that external provider credentials are fresh per-physical-attempt transport callbacks
bound to exact endpoint/profile/final-body digest/deadline and never long-lived SDK client state.

## Errors and edge cases

Never imply locked data is deleted/empty, OS keyring automatically falls back, MCP can unlock, a
boolean proves human presence, service-manager start means ready, or encryption protects from the
excluded live-account adversary. Troubleshooting uses bounded reasons, not paths/account names.
Never tell an existing-vault user to run initialization after a missing keyring entry/wrong
passphrase/tamper failure.
Never imply keyring storage success alone selects immutable mode or that
`human_authority_unavailable` means keyring corruption. Setup must show explicit retry and
passphrase-initialization choices without starting either automatically.

## Invariants

1. Claims are no stronger than executable specs/tests and public claim map.
2. No private strategy/business material or secret example appears.
3. Ordinary and confidential channels remain visibly distinct.
4. Headless/native-vault limitations are explicit.
5. First-install initialization and later unlock are visibly distinct, one-way ceremonies.
6. The challenge creator and one-secret parser are distinct and reachable only through the trusted
   human helper flow.
7. Pristine keyring initialization and existing-keyring ready-local load are visibly distinct.

## Tests

- `tests/conformance/claims/test_public_claim_map.py` maps every promise to ADR/spec/test evidence.
- `tests/packaging/test_private_boundary_and_secret_scan.py` scans examples for secret-like values
  and private references.
- `tests/conformance/claims/test_local_service_security_doc.py` checks required sections/states/
  forbidden surfaces, the two-capability first-install gate, existing-keyring ready-local wording,
  and no-overclaim language.

## Open questions

None.

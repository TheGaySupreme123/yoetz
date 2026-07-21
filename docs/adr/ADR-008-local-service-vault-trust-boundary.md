# ADR-008 — Local service and vault trust boundary

**Status:** Founder-selected working decision (2026-07-14). Binding for v0.1 specification work;
independent security review remains required before release.
**Related:** ADR-001 owns single-writer lifecycle; ADR-004 owns cryptography and recovery; ADR-006
and the privacy protocol own outbound data authorization.

## Context

The earlier draft treated `yoetz mcp serve` as a long-lived application owner and allowed a
standalone CLI command to open the same local state when MCP was absent. That made the integration
surface the trust boundary, required each process to acquire keys, made safe passphrase operation
through MCP impractical, and produced different behavior depending on which client started first.

## Trust boundary

| Component | Trust and authority |
|---|---|
| Per-user local Yoetz service | Trusted local authority. Sole owner of vault keys, decrypted state, catalog/task writers, provider gateway, and application facade. |
| Confidential human helper or future trusted desktop prompt | Narrowly trusted for one exact YZH1 ceremony and, when required, delivery of one YZS1 initialization, unlock, recovery, provider-credential, or reauthentication secret. It has no workflow, storage, provider-dispatch, policy, or long-lived key authority. |
| CLI, MCP bridge, future ordinary UI | Untrusted control/rendering clients. They may submit validated requests and receive bounded results; they cannot open storage, access key handles, unlock the vault, or weaken policy. |
| Agent, LLM, plugin, repository, imported transcript, provider | Untrusted content or external execution. Never receives an unlock secret or vault capability. |

Trusting the service does not imply trusting every local process. The service authenticates the
peer UID on an owner-only Unix-domain endpoint and exposes only an exact method allowlist. It never
accepts a caller-selected endpoint, filesystem path, key locator, provider object, or arbitrary
method name.

## Selected architecture

The v0.1 default is one persistent service per user and installation. The service starts in
`starting`, binds and authenticates its local control endpoint, attempts OS-keyring unlock, and
enters either `ready` or `locked`. It remains reachable while locked so a human can inspect
structural status and initiate the dedicated unlock flow. Only `ready` admits workflows,
maintenance, imports, payload access, or egress.

On a pristine first install, keyring storage usability alone does not select immutable keyring
mode. Before creating a staging artifact, IVK, keyring entry, or mode marker, the service requires
both an exact approved create-if-absent/round-trip keyring cell and an artifact-verified
action-bound `UserPresencePort` capability for the same installed release cell. The presence
capability's adapter/profile/platform identity and evidence digest must match the packaged support
allowlist, and its authenticated-prompt, trusted-action-binding, one-use-attestation, and available
states must all be active. No platform-name inference or caller boolean is accepted.

If the keyring is usable but that presence cell is absent, unavailable, inconclusive, stale, or
mismatched, the service performs no keyring/vault write and remains `uninitialized/locked` with
bounded `human_authority_unavailable` setup-required status. If the keyring itself is unavailable
or unusable, it likewise remains uninitialized/locked with its bounded keyring reason. A local
human may explicitly choose `service initialize-passphrase`, which uses a distinct
`vault_initialize` confidential purpose; neither branch silently falls back. The helper confirms
the passphrase twice locally but transmits one one-shot secret. The service accepts it only after
re-proving no committed installation identity/catalog/mode, vault ciphertext/sentinel, or ambiguous
staging state, then allocating a fresh installation ID. A successful keyring query that somehow
finds a correlated entry for that exact new identity blocks; a locked/unavailable backend is
recorded but does not block this explicit proven-pristine choice. The service then atomically commits
the passphrase envelope, empty-vault sentinel/layout, installation ID, and immutable mode.
Later unlock uses the distinct `vault_unlock` purpose. Initialization can never reset/recover an
existing vault or act as fallback for a missing keyring entry; crash ambiguity fails closed without
deleting or replacing data.
Committed passphrase mode never probes or uses keyring during startup. Later foreign/stale entries
are ignored and not deleted; an explicitly discovered exact-identity contradiction is quarantined.

Keyring usability is not action-bound human authorization. Under the resolved F-010 decision,
the first-install gate above prevents a new immutable keyring-mode choice when no verified
`UserPresencePort` exists. Existing keyring vaults created by an earlier supported release or
restored through reviewed migration remain readable: they may become ready for locally permitted
work without current presence, but external activation, privacy widening, provider-credential
set/rotate, and other durable authority changes stay fenced. No TTY click, same-UID peer, or
ordinary keyring unlock substitutes for the missing capability. Alternate verified platform
adapters, a separately designed admin-authorization secret, or a different first-install/migration
design require a new ADR; they are not implicit v0.1 fallbacks.

Normal clients use `ServiceClient` over the ordinary control protocol. MCP is a stdio-to-local-
service bridge: its process owns MCP framing but no application, key, storage, or provider state.
The CLI is the same kind of client except for `service run` (the daemon entrypoint) and
`service unlock` (the dedicated human helper). A future UI uses the same normal protocol; a future
trusted unlock prompt must use the separately typed unlock contract.

The service may hold decrypted state and opaque key or provider-credential handles only in process
memory. Clients never receive them. All secret buffers cross the swappable `SecretMemoryPort`:
bounded mutable buffers, tested page-lock/no-core-dump hardening where the platform supports it,
one-shot consumption, and best-effort overwrite. Python and provider libraries can still make
uncontrollable copies; neither Python nor the OS can prove perfect zero-copy handling or complete
zeroization. On shutdown or relock, the service stops admission, closes provider and storage
handles, and clears those handles and buffers best-effort.

Locked startup never opens the encrypted catalog merely to obtain a generation. Under the
per-install singleton, lifecycle advances a fixed owner-only nonsecret generation record at
`config/paths.service_generation_path()` using canonical digest, atomic replace, file fsync, and
directory fsync before publishing endpoints. After unlock the catalog CAS verifies/copies that
already advertised generation; contradiction/rollback terminates rather than changing generation
behind connected clients.

## Control and unlock channel separation

The ordinary control channel carries canonical, bounded workflow/support envelopes and structural
service status. Its method registry contains no unlock method and its request models cannot carry
secret bytes.

The service exposes three fixed owner-only same-UID endpoints: ordinary control; YZS1 one-secret
ingress; and YZH1 multi-phase human control. YZH1 is the sole challenge/binding creator, returns
bounded structural previews, supports zero-secret keyring retry, coordinates provider credential
set/rotate, accepts typed privacy decisions, and owns the exact ceremony for changing or disabling
idle relock. Durable policy widening, credential changes, and idle-security weakening
require internal strong reauthentication; a `confirm_every_request` decision already inside the
durable policy uses exact foreground digest-bound consent and creates no reusable authority. YZS1
accepts only a binding from one still-live YZH1 ceremony and never accepts a zero-length retry or
creates a challenge. Ordinary control/MCP schemas and their import graphs expose no route to either
confidential client. This is not a claim that arbitrary malicious same-UID code with socket access
cannot emulate a YZH1 client; that limitation is explicit below and in resolved decision F-011.

The confidential secret-ingress channel is separately typed by a closed seven-value `SecretPurpose`
registry: `vault_initialize`, `vault_unlock`, `portable_recovery`,
`provider_reauthentication`, `provider_credential`, `privacy_reauthentication`, and
`security_reauthentication`. The seventh value is used only by an exact idle-relock-policy target;
it cannot substitute for provider/privacy authority. The channel accepts
only one-shot material for the exact live ceremony without placing bytes in the ordinary control
envelope.
Initialize is available only for a pristine uninitialized installation and accepts one passphrase
after the helper's local two-entry match; unlock is available only for an already committed locked
passphrase vault. Their handles are not interchangeable. The pure confidential protocol maps the
wire purpose to one one-shot `SecretHandle(SecretPurpose.vault_unlock)` consumed by the trusted
unlock coordinator; ordinary clients cannot construct it. The helper must prove a same-user,
foreground controlling TTY; read directly from `/dev/tty` in no-echo mode; reject stdin,
redirection, pipes, environment/config/argument input, and noninteractive execution; establish a
separately typed peer-authenticated connection; and erase mutable buffers best-effort after the
service consumes them. No MCP registry, `ServiceClient`, or public application method can reach
this channel. **Amendment (ADR-015/016):** the founder-authorized `yoetz consent` /
`elevated-bootstrap` path may, after exact digest-bound human consent, admit secrets for
catalogued `secret_ingress` operations (`vault_initialize`, `provider_credential_set`,
`provider_credential_rotate`) only from inherited file descriptors on `approve` — still never via
MCP, argv, environment secret values, config, or chat paste. Provider credentials become opaque adapter-scoped handles in the service vault;
provider adapters and normal clients never receive reusable credential bytes.

`UnlockCoordinator` solely owns the persistent passphrase throttle: admission delay, in-progress
reservation, failure charge, success reset, crash re-arm, and repair. It calls cryptographic vault
verification only after reserving the attempt. `VaultService` never reads or mutates that throttle
and is the sole minter of `HumanAuthorizationProof`; presence adapters and coordinators can only
supply an exact source/challenge for the vault to consume. This division applies equally to
unlock, provider, privacy, and security reauthentication.

Peer UID checks and owner-only filesystem permissions protect local transport from other users.
They cannot distinguish a malicious process already running as the same user; that is outside the
stated threat model. TTY checks establish an explicit local-human ceremony, not a cryptographic
proof that automation is impossible, and public documentation must say so.

## Platform evidence and claim limits

The selected boundary is grounded in platform capabilities, but the existence of an API is not by
itself a Yoetz support claim:

- Apple documents Keychain as storage for passwords, keys, certificates, and other small secret
  data protected by access controls and data-protection classes
  ([Apple Platform Security](https://support.apple.com/guide/security/keychain-data-protection-secb0694df1a/web)).
  Yoetz still has to verify the exact backend, accessibility class, user-presence behavior, and
  locked-session behavior in each advertised macOS release cell.
- The Freedesktop Secret Service protocol explicitly models locked items/collections and an
  implementation-specific prompt path for unlocking
  ([Secret Service API, Unlocking Objects](https://specifications.freedesktop.org/secret-service/latest/unlocking.html)).
  This supports a `locked` service state; it does not justify claiming that every Linux desktop or
  headless session has a usable, unlocked, or approved keyring.
- Linux Unix-domain sockets expose peer credentials through `SO_PEERCRED`
  ([unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)); macOS exposes the effective peer
  identity through `getpeereid`
  ([Apple manual page](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/getpeereid.3.html)).
  Release tests must prove the exact adapter behavior, fail-closed handling, endpoint ownership,
  and replacement resistance on every advertised platform.
- Python's `getpass` documentation permits a warning followed by input from `sys.stdin` when
  echo-free input is unavailable ([Python documentation](https://docs.python.org/3/library/getpass.html)).
  That fallback violates this trust boundary. The confidential helper therefore opens `/dev/tty`
  itself, verifies the foreground controlling terminal, enables no-echo input, and fails closed if
  any part of that ceremony is unavailable; it never delegates fallback selection to `getpass`.

These sources justify the architecture and its required probes, not broad statements such as
"the OS keyring is always secure" or "same-user local IPC is safe." Public claims remain limited to
exact, measured package/platform/backend cells recorded by the release evidence matrix.

## Options evaluated

### A. Persistent service owns vault and writers — selected

This gives one unlock ceremony, stable CLI/MCP/UI behavior, one privacy-policy enforcement point,
and one durable writer topology. The added local protocol and service lifecycle are justified by
the security and concurrency boundary they create.

### B. Separate key broker plus per-client application processes — rejected for v0.1

A key broker that exports keys violates the boundary. A broker that performs cryptography by RPC
requires every object operation to cross a second protocol while independent application
processes still compete for catalog and bundle writers. It is more complex without satisfying the
single-authority product model.

### C. Per-client keyring/passphrase unlock — rejected

CLI and MCP processes would handle keys or prompts, an MCP agent could influence the unlock path,
multiple clients would retain decrypted state, and service behavior would depend on client
lifetime. A noninteractive MCP process has no acceptable human prompt.

### D. Inherited secret descriptor for headless passphrase unlock — evaluated, not selected as a *generic* path

An anonymous, one-shot descriptor inherited directly from an explicitly configured trusted
supervisor can avoid argv/env/config/history exposure, but descriptor provenance, inheritance,
buffering, lifetime, and platform supervisor semantics require a dedicated design and review.
Resolved decision F-008 requires unattended readiness but does not require unattended passphrase
transport. v0.1 therefore supports headless readiness only for an already initialized verified
OS-keyring vault. A pristine headless install cannot auto-select keyring mode without the exact verified
presence cell, and a passphrase-locked headless service remains locked; there is no generic
password-fd option. **Amendment (ADR-015/016):** after exact digest-bound human consent, the
elevated consent approve path may admit secrets on inherited FDs only for catalogued
`secret_ingress` / `secret_reauth` operations (implemented: vault initialize and provider
credential set/rotate). This is not a generic headless unlock FD API and does not unlock an
already-locked vault without a local TTY ceremony.

### E. Native vault subprocess — stronger remaining option, not selected for v0.1

A small memory-safe native subprocess could own keys and perform cryptographic or credential
authorization operations over a narrow RPC, reducing accidental copies in the Python service. It
would not protect against the compromised active UID/root/live-memory adversaries excluded by the
threat model, and it adds a second lifecycle, protocol, packaging, and crash-recovery boundary.
v0.1 uses the in-process service vault behind `SecretMemoryPort`; the port intentionally permits a
future native implementation without changing CLI/MCP/application contracts. Requiring the native
vault before first release is the one materially stronger containment choice still available.

## Security invariants

1. Only the ready service imports concrete storage, key, object, provider, and application
   composition modules.
2. Normal clients never receive or submit a key, passphrase, recovery secret, decrypted object
   handle, or policy-bypass capability.
3. Locked is an available service state, not a startup crash and not an empty-data mode.
4. The service never silently changes vault mode or falls back from keyring to passphrase.
5. Loosening privacy policy and unlocking the vault require trusted local-human surfaces; MCP,
   agents, and LLMs can only report that authorization is needed.
6. The local control transport is never TCP, network-reachable, project-relative, or selected by
   caller content.
7. A second service, stale generation, or unauthenticated peer cannot open a writer or decrypt a
   vault record.
8. First-install initialization and later unlock have distinct purposes. Existing mode,
   ciphertext, sentinel, keyring state, or ambiguous staging forbids initialization; no mode reset
   or key replacement is inferred.
9. Service generation is durable before locked endpoint publication and requires no catalog/key;
   corrupt or rolled-back generation metadata fails closed.
10. Pristine automatic keyring initialization mutates nothing unless the same installed artifact
    proves both the approved keyring cell and exact action-bound user-presence cell. Existing
    keyring vaults may unlock without current presence only into ready-local, externally fenced
    authority.
11. Idle relock can be disabled only through a YZH1 `idle_relock_policy_change` ceremony and a
    vault-minted proof for its exact current/proposed target; ordinary/MCP/config input cannot do
    so.

## Locking and reauthorization lifecycle

- Explicit `service lock` stops admission immediately, cancels noncommitting work, lets already
  admitted shielded database commits reach a definite bounded outcome, closes provider and
  decrypted-state handles, and returns to `locked`. MCP does not expose this method.
- An advertised platform session-lock or system-suspend event takes the same path. Wake or user
  session unlock never changes the service to `ready`; the human/keyring ceremony runs again.
- Idle relock is enabled by default only when there are no connected clients, in-flight requests,
  queued commits, leases, or provider calls for the complete configured interval. It never counts
  a long-running operation as idle. The default is 900 seconds. The only v0.1 mutation path is a
  foreground YZH1 `idle_relock_policy_change` preview over the exact current/proposed value,
  followed by action-bound OS user presence or the distinct passphrase-mode
  `security_reauthentication` purpose. It may select 60..86400 seconds or explicit `disabled`;
  server-side `edit` does not exist. The service consumes the exact vault-minted proof atomically
  with the change. The exception lasts only for the current service generation, is never persisted,
  and restart restores 900 seconds. Disabling idle relock never disables explicit, session-lock,
  suspend, or monitor-loss relock.
- Privacy-policy loosening is not authorized by a boolean CLI flag or ordinary control request.
  It requires a fresh OS user-presence assertion or a confidential vault reauthentication proof
  bound to the exact proposed policy digest and expiry. MCP/LLMs may request more context but
  cannot mint or relay that proof.
- If the service is absent, normal CLI reports `service_unavailable` and MCP returns a sanitized
  structured unavailability result; neither starts a hidden runtime. If it is locked, both report
  `vault_locked` and direct the local human to the separate unlock surface without accepting a
  secret.

## Consequences

Persistent local service, control protocol, client library, vault state machine, unlock helper,
and their crash/secret-boundary tests move into v0.1. Native launchd/systemd-user convenience and
passphrase-based headless unlock remain separate future decisions; neither may be approximated by
client-side direct access or a secret in argv/env/config/stdin.
The v0.1 helper also owns the explicit first-install passphrase initialization ceremony: two local
no-echo entries, one transmitted `vault_initialize` secret, atomic vault commit, and fail-closed
crash recovery.
Automatic pristine keyring initialization is therefore an artifact-gated convenience, not a
keyring-only default. Any future admin secret, additional platform presence adapter, or migration
that changes the resolved F-010 authority model requires an explicit ADR amendment.

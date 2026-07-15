# src/yoetz/service/secret_ingress.py — confidential local-human secret ingress

**Wave:** C/D | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):**
`service/confidential_protocol.md`, `ports/secret_memory.md`, `service/lifecycle.md`,
`adapters/control/unix_socket.md` | **Imported by:** `service/daemon.md`, `service/unlock.md`,
`service/human_control.md`

## Purpose

Provides the only path by which a local human-control session can deliver first-install vault
initialization, later vault unlock, portable-recovery, provider-credential, or
privacy-reauthentication bytes to the trusted service. It is physically and
type-wise separate from ordinary CLI/MCP control and never represents secrets as JSON, argv,
environment, config, stdin, logs, or application values.

## Public surface

- `class SecretIngressService` with async `serve`, `accept_once`, `cancel_pending`, and `close`.
- `class SecretIngressError(Exception)` — bounded reasons `tty_required`, `peer_untrusted`,
  `purpose_forbidden`, `state_forbidden`, `binding_invalid`, `binding_expired`,
  `secret_too_large`, `partial_frame`, `rate_limited`, `cancelled`.

## Behavior

The service binds the second owner-only Unix socket beneath the verified runtime directory; the
third human-control endpoint in `service/human_control.md` is the sole challenge creator. Both
sides verify same effective UID. The normal control listener does not forward to this endpoint and
the MCP package cannot import `ConfidentialSecretClient` (enforced by import-graph tests).

One connection carries exactly the YZS1 frame owned by `confidential_protocol.md`; this server owns
only binding lookup, state/purpose admission, protected capture, and internal completion. Lengths
are checked before allocation; zero/oversize/extra/partial bytes fail and close. The binding is
structural canonical data minted by the still-open matching human-control ceremony, previously
presented to the human, includes the current
service instance/generation and one-use challenge, and is authenticated by the same-UID endpoint
session. Secret bytes are read directly into a bounded mutable allocation, captured immediately by
`SecretMemoryPort`, and overwritten in the receive buffer in `finally`. They are never decoded as
JSON or logged. Vault/recovery/reauthentication passphrases repeat the exact shared 16..1,024-byte
strict-UTF-8/no-NUL-CR-LF/no-normalization validation inside their purpose consumer; provider
credential bytes first pass the generic 1..8,192-byte/no-NUL-CR-LF guard and then the exact
validator owned by the installed provider/endpoint profile named in `ProviderCredentialBinding`.
The exact validator runs before encrypted vault storage and again before one-attempt transport
injection; absent/mismatched validators reject and never log/normalize/store the input.

Purpose/state rules are exact: `vault_initialize` only an active initialization challenge while
the service is locked in `uninitialized` mode and the vault has re-proven no committed mode,
ciphertext, sentinel, keyring entry, or partial staging artifact; `vault_unlock` only locked
passphrase mode;
`portable_recovery` only after a secret-free maintenance preview was shown and the local human
confirmed its exact request ID plus `plan_digest`; its challenge is bound to both and is consumed
by that one execute attempt. Its binding also freezes `create|restore`: create requires helper-side
double entry but transmits one frame; restore transmits one single-entry frame. The service receives
one handle in either case and never receives confirmation bytes. `provider_reauthentication` is
only a ready service and exact pending
credential ceremony; `provider_credential` only that same ceremony after its fresh internal
human-authorization proof, with exact provider/endpoint/scope/purpose binding;
`privacy_reauthentication` only for an exact pending policy/disclosure digest presented through
`service/human_control.md`. Challenges expire after 60
seconds, are single-use, and at most one unlock attempt runs at a time. Failure rate limits use
structural counters/timers and reveal no secret-derived detail.

YZS1 never creates/returns a challenge, preview, or keyring result. Zero-length secret frames remain
invalid. Zero-secret keyring retry is a typed action on the YZH1 human-control endpoint; provider
set/rotate and privacy decisions also begin there before this parser can accept their secret phase.

## Errors and edge cases

- TTY verification is performed by the local helper; service-side same-UID authentication cannot
  cryptographically prove a human. Public documentation states this threat limit.
- Ordinary stdin, pipes, files, environment, args, shell substitution, MCP frames, or normal
  control frames are never fallback sources.
- Disconnect/timeout consumes the challenge; retry requires a new ceremony.
- A service generation/state change between binding and secret receipt rejects and overwrites the
  bytes without attempting unlock/use.
- An initialization handle is never retyped as an unlock handle. Existing `os_keyring` or
  `passphrase` mode, any committed ciphertext, or ambiguous partial first-install state rejects
  `vault_initialize` before KDF work and preserves every byte on disk.
- Preview, decline, stale-plan recomputation, and ordinary confirmation never accept or stage a
  portable-recovery secret. If request/plan digest changes after staging, the handle is consumed/
  overwritten unused and a new preview-confirmation-secret ceremony is required.
- No general `password-fd` or inherited descriptor exists in v0.1.

## Invariants

1. Exactly one bounded secret crosses each confidential connection and is captured once.
2. Purpose and exact target/policy binding are fixed before secret bytes are read.
3. Ordinary clients, application methods, provider cases, and MCP cannot construct this path.
4. No secret-derived value or reusable authorization proof enters a response; success/failure is
   structural and bounded.
5. Headless inherited-secret input is absent from v0.1.
6. First-install initialization is a distinct one-shot purpose and cannot serve as keyring
   fallback for an existing vault.
7. A YZS1 frame is accepted only for a live YZH1 ceremony binding; the secret endpoint is never a
   standalone helper API.
8. Passphrase/recovery byte policy is revalidated by the service; helper comparison never replaces
   service validation and confirmation bytes never cross YZS1.
9. Provider credentials cannot be stored under a generic rule: their exact installed profile owns
   a stricter validator within the 8,192-byte transport ceiling.

## Tests

- `tests/subprocess/test_service_secret_boundary.py` covers pipes/non-TTY rejection, framing,
  oversize/partial/extra bytes, same-UID checks, process metadata, logs, and transcript canaries.
- `tests/integration/service/test_secret_ingress.py` covers every purpose/state/binding/generation/
  expiry/rate-limit combination.
- `tests/packaging/test_service_boundary_imports.py` proves MCP and ordinary client import graphs
  cannot reach this server; the confidential helper imports only `confidential_client.md`.

## Open questions

None.

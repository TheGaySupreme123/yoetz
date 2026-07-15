# src/yoetz/service/confidential_protocol.py — pure YZH1/YZS1 confidential wire contract

**Wave:** C/D | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):**
`protocol/canonical.md`, `protocol/ids.md`, `domain/values.md` | **Imported by:**
`service/confidential_client.md`, `service/human_control.md`, `service/secret_ingress.md`,
`adapters/control/unix_socket.md`, confidential protocol tests

## Purpose

Own the complete client-safe framing and closed structural types for the multi-phase local-human
channel (`YZH1`) and the one-secret channel (`YZS1`). It contains no socket connector, terminal
reader, service authority, vault/application/key/provider import, or effectful method.

## Public surface

- Constants `HUMAN_PROTOCOL_MAGIC = b"YZH1"`, `HUMAN_PROTOCOL_VERSION = 1`,
  `MAX_HUMAN_CONTROL_FRAME_BYTES = 65_536`, `SECRET_PROTOCOL_MAGIC = b"YZS1"`,
  `SECRET_PROTOCOL_VERSION = 1`, `MAX_SECRET_BYTES = 16_384`, and
  `MAX_SECRET_BINDING_BYTES = 4_096`; plus `PASSPHRASE_MIN_BYTES = 16` and
  `PASSPHRASE_MAX_BYTES = 1_024`, and `PROVIDER_CREDENTIAL_MAX_BYTES = 8_192`.
- Closed `HumanCeremonyKind`: `vault_initialize`, `vault_unlock`, `keyring_retry`,
  `portable_recovery`, `provider_credential_set`, `provider_credential_rotate`,
  `privacy_policy_decision`, `privacy_disclosure_decision`.
- Closed `ConfidentialSecretPurpose` with fixed wire codes: `1=vault_initialize`,
  `2=vault_unlock`, `3=portable_recovery`, `4=provider_reauthentication`,
  `5=provider_credential`, `6=privacy_reauthentication`.
- `HumanOpenTarget`, a tagged union of exactly `EmptyVaultTarget`, `PortableRecoveryTarget`,
  `ProviderCredentialTarget`, and `PrivacyPendingTarget` as defined below.
- `HumanPreview`, a tagged union of exactly eight ceremony-specific preview types; `HumanAction`,
  a union of exact `retry`, `select_authorization_source`, `decision`, and `cancel` branches;
  `HumanPhase`, a union of `secret_required` and `authorization_required`; `HumanResult`, a union
  of `vault_state`, `keyring_retry`, `portable_recovery`, `provider_credential`, and
  `privacy_decision` results.
- Frozen `HumanCeremonyBinding`, `HumanDecisionBinding`, and `SecretIngressBinding` structural
  values. Every challenge/correlation is exactly 64 lowercase hexadecimal characters from 32
  independent OS-CSPRNG bytes.
- Closed YZH1 envelope union: `ClientOpenEnvelope`, `ServerOpenedEnvelope`,
  `ClientActionEnvelope`, `ServerPhaseEnvelope`, `ServerResultEnvelope`, `ServerErrorEnvelope`,
  `ClientCancelEnvelope`, and `ServerCloseEnvelope`.
- Pure `encode_human_frame`, `decode_human_frame`, `encode_secret_header`,
  `decode_secret_header`, `validate_passphrase_buffer(memoryview)`, and closed
  `ConfidentialProtocolError`.

## Behavior

Each YZH1 frame is exactly
`magic[4] | version:u8 | frame_type:u8 | payload_length:u32be | canonical_UTF8_JSON`.
Frame-type codes are frozen: 1 client-open, 2 server-opened, 3 client-action, 4 server-phase,
5 server-result, 6 server-error, 7 client-cancel, 8 server-close. The cap excludes the ten-byte
header. Length is rejected before allocation. JSON forbids unknown/duplicate fields, floats, BOM,
NUL, lone surrogates, and noncanonical encodings. One connection owns one ceremony. Client-open
contains protocol version, one 64-hex connection nonce, ceremony kind, and one exact target.
Server-opened creates the 64-hex ceremony ID/binding and returns one exact preview plus next phase.
Every later envelope repeats that ceremony ID and an integer step starting at 1; steps increase by
one and cannot be skipped/replayed. A terminal result or error is followed by exactly one close;
EOF before close is ambiguous, never success.

Open targets are closed:

- vault initialize/unlock/keyring retry use `{kind:"vault", expected_mode}` and no locator;
- portable recovery uses `{kind:"portable_recovery", operation:"create"|"restore", request_id,
  confirmed_plan_digest}`; create means new portable artifact and restore means open one;
- provider credential uses `{kind:"provider_credential", action:"set"|"rotate", provider_id,
  model_id, endpoint_profile_id, endpoint_profile_version, scope_digest, purpose_digest}`;
- privacy uses `{kind:"privacy_pending", decision_kind:"policy"|"disclosure", pending_id}`.

The eight preview branches repeat only bounded structural facts needed for the decision. Vault
initialize names the irreversible mode selection; vault unlock names current mode; keyring retry
names `pristine_create|existing_load`; recovery names operation/request/plan plus location/content
commitments and bounded counts; credential set/rotate names its exact binding; policy decision
contains the exact diff digest/categories/scopes; disclosure decision contains exact minimized
excerpt/category/destination commitments, byte/token counts, policy digest, and
`authorization_change:"none"`. A disclosure outside durable effective policy is not representable
by that branch and must use a separately reauthenticated policy transition. Preview fields never
contain a secret, raw credential, key locator, unrestricted transcript, environment, or plaintext
outside the bounded human-approved excerpt preview.

Actions are exact: keyring accepts only `retry`; provider/policy durable-authority ceremonies accept
`select_authorization_source` with `os_user_presence|secret_reauthentication`; privacy decisions
accept only `approve|deny|edit`; cancel is universal. A `confirm_every_request` disclosure already
inside durable policy accepts the foreground digest-bound decision directly and does not request
OS/passphrase reauthentication. Provider credential set/rotate and policy widening require the
authorization phase. Server phases either carry one `SecretIngressBinding` or describe strong OS
presence availability plus whether an established passphrase reauthentication path exists; they
never carry authority/proof bytes.

Result branches are exact and structural: vault/keyring state plus bounded reason; portable
operation/status/result commitment; credential action/stored generation/activation status;
privacy committed/denied/stale plus policy/receipt commitment. Server-error code is one of
`protocol_mismatch`, `invalid_frame`, `frame_too_large`, `peer_untrusted`, `kind_forbidden`,
`target_invalid`, `state_forbidden`, `stale_generation`, `binding_expired`, `phase_invalid`,
`replay`, `presence_unavailable`, `reauthentication_unavailable`, `reauthentication_required`,
`action_denied`, `secret_rejected`, `cancelled`, `internal_error`; it contains only `retryable` and
the ceremony ID when one exists. Close outcome is `completed|cancelled|failed`.

Each YZS1 connection carries exactly one frame:
`magic[4] | version:u8 | purpose:u8 | binding_len:u16be | secret_len:u32be |
binding_canonical_JSON | secret_bytes`. `SecretIngressBinding` has exactly binding version,
ceremony ID, secret challenge, wire purpose, service instance/generation, vault generation,
nullable policy generation, exact target digest, and bounded expiry. The server validates the
binding before allocating/reading secret bytes. Zero, oversize, partial, and extra bytes fail.
YZS1 is one-way: after exact EOF the server consumes or rejects internally and closes with zero
response bytes; the matching YZH1 session alone returns phase/result/error/close.

The shared passphrase validator accepts 16 through 1,024 bytes inclusive, requires one exact strict
UTF-8 sequence, and rejects U+0000, U+000A, and U+000D. It removes nothing: no trimming, Unicode
normalization, case folding, replacement decoding, NUL termination, or implicit newline is allowed.
The TTY line delimiter is consumed separately and never enters the buffer. Validation operates over
mutable memory/an incremental decoder and retains no decoded string. Vault initialization,
portable-recovery creation, later unlock/restore, and established-passphrase reauthentication all
use this exact rule on both helper and trusted-service sides.

Purpose validation is closed. `vault_initialize`, `vault_unlock`, `portable_recovery`,
`provider_reauthentication`, and `privacy_reauthentication` accept only the passphrase policy
above. `provider_credential` accepts a generic transport/storage guard of 1..8,192 opaque bytes and
rejects NUL, CR, and LF before profile lookup; then the exact installed provider/endpoint profile
must apply its owned, stricter credential validator before encryption/storage and again before one-
attempt header injection. A missing validator is `secret_rejected`, never permissive fallback.

## Errors and edge cases

- Wrong magic/version/type/direction, open after open, crossed ceremony/step, result without close,
  YZH1 secret bytes, YZS1 JSON action, and purpose/binding mismatch are fatal.
- A helper cannot mint a binding: YZS1 accepts only one still-live binding registered by the
  matching server-side YZH1 ceremony. The serialized binding is not a bearer capability outside
  that live same-UID session and generation.
- `MAX_SECRET_BYTES` is only the absolute transport guard. Purpose consumers apply the stricter
  passphrase/credential rules; protocol acceptance never means semantic acceptance.
- All dataclasses have strict constructors, `slots`, frozen state, redacted bounded repr where a
  preview may contain approved excerpts, and no arbitrary metadata/options mapping.

## Invariants

1. This module is pure and safe in CLI/MCP import graphs; it imports no service authority.
2. YZH1 carries structural preview/action/result only; YZS1 carries exactly one bounded secret.
3. Every post-open frame is ceremony- and step-correlated, and every terminal sequence closes.
4. No envelope represents an ordinary workflow call, reusable authorization proof, or generic
   endpoint/path.
5. Confirm-every-request consent cannot widen durable policy; durable authority changes require
   strong presence or an established reauthentication path.

## Tests

- `tests/unit/service/test_confidential_protocol.py` freezes golden bytes for all eight opens,
  eight previews, all phases/actions/results/errors/close, all six YZS1 purpose codes, correlation,
  every malformed/cap boundary, passphrase byte/UTF-8/no-normalization vectors, and generic
  credential 0/1/8,192/8,193 plus NUL/CR/LF vectors.
- Import-graph tests prove this module imports only pure protocol/domain values.

## Open questions

None.

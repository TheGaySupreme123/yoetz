# src/yoetz/service/elevated_bootstrap.py — ADR-015 elevated-bootstrap pending consent store

**Wave:** D | **ADRs:** ADR-015 | **Imports (spec-tree):** `config/paths.md`,
`protocol/canonical.md`, `protocol/json_types.md` | **Imported by:** `cli/elevated.md`,
agent status projection

## Purpose

Owns the founder-authorized elevated-bootstrap consent challenge for cloud-agent first-run setup.
It creates and validates one ephemeral owner-only pending record, exposes only structural status for
agents, reads approved secrets only from inherited file descriptors, and appends structural audit
events without secret material. It is the ADR-015 exception to the ordinary ADR-008 TTY ceremony,
not a general headless vault or provider-secret API.

## Public surface

- `ElevatedOperation` is exactly `vault_initialize | provider_credential_set`.
- `PendingElevatedConsent` is the frozen pending record: `pending_id`, operation, `danger_text`,
  `danger_digest`, `confirmation_phrase`, creation/expiry Unix times, `target_digest`, and optional
  nonsecret provider binding.
- `prepare_pending(operation, target_digest, provider_binding=None)` creates a pending consent.
- `load_pending()`, `clear_pending()`, and `approve_pending(pending_id, danger_digest, confirm)`
  own pending lifecycle and exact approval checks.
- `projection_for_status(pending)` and `status_payload()` return the agent-safe structural view.
- `read_secret_fd(fd, maximum)` reads one bounded secret from an inherited descriptor.
- `ElevatedBootstrapError.reason` is the stable bounded failure token.

## Behavior

`prepare_pending` accepts only `vault_initialize` and `provider_credential_set`. It rejects a
provider binding for vault initialization and requires one for provider credential set. When no
unexpired pending record exists, it creates an owner-only pending file under the elevated-bootstrap
state directory with a random `pending_id`, ADR-015 danger text, `danger_digest`, random exact
confirmation phrase, `target_digest`, and a fifteen-minute TTL. The pending payload contains no
passphrase, reauthentication secret, provider credential, bearer token, proof, socket path, or file
descriptor value. Active pending consent is singleton; expired pending state is cleared before a new
request can proceed.

The `danger_digest` is a domain-separated canonical digest over the danger text, phrase, operation,
pending ID, target digest, expiry, and provider binding when present. `load_pending` decodes the
owner-only state, validates the operation and nonsecret binding shape, recomputes the digest, clears
and audits expired records, and fails closed on corrupt or tampered state.

`approve_pending` does not read secrets and does not complete the operation. It loads the live
pending record and verifies the exact `pending_id`, exact `danger_digest`, and byte-for-byte exact
confirmation phrase. On acceptance it appends an audit event with structural identifiers only and
returns the pending record to the CLI approve driver. Callers must clear pending state after either
successful operation completion or any later approval/operation failure.

`read_secret_fd` accepts only inherited descriptors other than `0`, `1`, and `2`. It reads until EOF
or the configured maximum plus one byte, rejects empty and oversized input, strips one trailing LF or
CR delimiter when present, returns a mutable `bytearray`, and overwrites intermediate storage on
failure. It never reads stdin and never accepts argv, environment, config, MCP, transcript, or chat
paste as a secret channel.

`projection_for_status` is the only agent-facing view. Without pending state it reports
`required:false`, `state:not_prepared`, empty structural identifiers, and `forbidden_channels`.
With pending state it reports `danger_text`, `danger_digest`, `confirmation_phrase`, expiry,
operation, pending ID, an approve-command template using FD placeholders `3` and, for provider
credential set, `4`, plus `forbidden_channels` listing `mcp`, `argv`, `env`, `stdin`, `config`, and
`transcript`. It does not include secrets, local paths, tokens, proofs, or descriptor contents.

Audit JSONL is owner-only append state. Events record structural facts such as prepare, expire, and
accepted approval with timestamps, operation, pending ID, digest, and expiry as applicable. Audit
records never include passphrases, provider credentials, reauthentication secrets, entered length,
FD contents, bearer tokens, proofs, or chat/transcript material.

## Errors and edge cases

Invalid operation, wrong provider-binding presence, active pending consent, corrupt JSON, malformed
binding, digest mismatch, expiry, missing pending state, pending ID mismatch, danger-digest
mismatch, confirmation mismatch, unsafe or reserved FD numbers, empty secret, oversized secret, and
read/clear failures all raise `ElevatedBootstrapError` with bounded reason strings. Expiry clears
pending state and audits the expiration. A same-UID attacker can still race local files; this module
records founder intent and enforces the ADR-015 orchestration boundary, not cryptographic exclusion
of malicious same-UID code.

## Invariants

1. Elevated bootstrap is scoped only to `vault_initialize` and `provider_credential_set`.
2. Pending consent contains danger text, digest, phrase, target digest, and TTL, but no secrets.
3. Approval requires exact pending ID, exact danger digest, and exact phrase before any secret read.
4. Secret bytes can enter only through inherited FDs other than `0`, `1`, and `2`.
5. Agent status projection is structural and includes danger text, approve-command template, and
   forbidden channels, but never secrets, paths, tokens, or proofs.
6. Pending state is one active record and must be cleared after approve success or failure.
7. Audit JSONL records structural consent events without secret material.

## Tests

- `tests/unit/service/test_elevated_bootstrap.py` freezes operation scope, pending payload/digest
  shape, TTL, singleton/expiry behavior, status projection, FD rejection/read bounds, clear-on-
  approval contract, and audit allowlist.
- CLI and subprocess tests cover inherited-FD operation completion and absence of secret flow
  through MCP, argv, env, stdin, config, transcript, or chat paste.

## Open questions

None.

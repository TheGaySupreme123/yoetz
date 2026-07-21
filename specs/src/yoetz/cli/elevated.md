# src/yoetz/cli/elevated.py — consent prepare/status/catalog/approve CLI driver

**Wave:** D | **ADRs:** ADR-008, ADR-015, ADR-016 | **Imports (spec-tree):** `cli/unlock.md`,
`service/elevated_bootstrap.md`, `service/confidential_client.md`,
`service/confidential_protocol.md`, `protocol/canonical.md`, `protocol/json_types.md` |
**Imported by:** `cli/app.md` (`elevated-bootstrap` and alias `consent`)

## Purpose

Provides the CLI-facing elevated consent workflow for cloud agents that lack a user-owned
controlling TTY. It prepares nonsecret consent challenges, renders structural status and the
consent catalog, and, only after exact human approval, drives the existing confidential
human-control ceremony using secrets read from inherited file descriptors for implemented
secret-ingress operations.

## Public surface

- `status_elevated()` / `catalog_elevated()` return service-owned JSON payloads.
- `prepare_elevated(operation, provider_binding=None, target_digest=None)` creates a pending
  challenge for an implemented catalog operation.
- `approve_elevated(pending_id, danger_digest, confirm, passphrase_fd=None, reauth_fd=None,
  credential_fd=None)` verifies exact pending consent (which consumes it) and completes
  vault initialize or provider credential set/rotate through inherited FDs.
- App wiring exposes both `yoetz elevated-bootstrap …` and `yoetz consent …`.

## Behavior

`status_elevated` / `catalog_elevated` delegate to the service module.
`prepare_elevated` accepts implemented ops only. For `vault_initialize`, it binds the empty-vault
target digest. For `provider_credential_set` / `provider_credential_rotate`, it requires the
nonsecret provider binding and binds action `set` or `rotate` into the canonical target digest.
Unimplemented catalog ops (phrase-only backup/restore/migrate, skill install, harness MCP register,
idle-relock, privacy widen) raise `operation_not_implemented`.

`approve_elevated` calls `approve_pending` first (single-shot consume). No file descriptor is read
until that check accepts. For `vault_initialize`, it requires `--passphrase-fd`. For credential
set/rotate, it requires `--reauth-fd` and `--credential-fd`. Descriptors cannot be `0`/`1`/`2`.
It drives the existing human-control ceremony, verifies preview digests, and maps
`ConfidentialClientError` / `HumanCeremonyCliError` to bounded `ElevatedBootstrapError` reasons.
Pending is already cleared by `approve_pending`; ceremony failure does not restore it (caller must
prepare again).

## Errors and edge cases

Missing operation-specific FDs, invalid reserved FDs, rejected secret bytes, confidential-client
errors, preview mismatch, unsupported phase, unavailable secret reauthentication, wrong or missing
secret purpose, exhausted ceremony steps, cancellation, invalid terminal result type, unimplemented
ops, and all pending-consent failures return bounded `ElevatedBootstrapError` reasons.

## Invariants

1. Prepare creates only nonsecret pending consent; approve is the only secret-consuming entry point.
2. No secret FD is read until pending ID, danger digest, and exact phrase have all matched.
3. Inherited secret FDs exclude `0`, `1`, and `2`; stdin/argv/env/config/MCP/transcript/chat are
   forbidden secret channels.
4. Implemented completion ops are `vault_initialize`, `provider_credential_set`, and
   `provider_credential_rotate` only.
5. Pending consent is consumed on approve accept; post-approval failure leaves it cleared.
6. The CLI returns only structural JSON and never prints, logs, stores, or audits secret material.

## Tests

- `tests/unit/cli/test_elevated.py` covers catalog flags, prepare/status projection, phrase
  placeholder, unimplemented phrase-only refusal, FD-required approve failure clearing pending.

## Open questions

None.

# tests/unit/cli/test_elevated.py — elevated-bootstrap CLI driver vectors

**Wave:** D | **ADRs:** ADR-008, ADR-015 | **Imports (spec-tree):**
`src/yoetz/cli/elevated.md`, `src/yoetz/service/elevated_bootstrap.md`,
`src/yoetz/service/confidential_client.md`, `src/yoetz/service/confidential_protocol.md` |
**Imported by:** unit suite

## Purpose

Freeze the CLI-side ADR-015 driver: status/prepare delegation, target digest construction,
approve-before-secret-read ordering, operation-specific inherited-FD requirements, confidential
ceremony sequencing, pending cleanup on success and failure, mutable-secret overwrite, and
structural JSON results.

## Public surface

Tests cover `status_elevated`, `prepare_elevated`, `approve_elevated`, target digest helpers, vault
initialize completion, provider credential set completion, the confidential phase driver, and quiet
cancellation. The suite uses scripted pending-store, FD reader, validator, human-control session,
secret-client, and result fixtures; it does not start the daemon or open real provider/vault state.

## Behavior

Assert `status_elevated` returns the service status payload unchanged. Assert `prepare_elevated`
binds `vault_initialize` to the canonical empty-vault target digest, binds
`provider_credential_set` to the exact nonsecret provider credential `set` target fields supplied by
the app parser, delegates to `prepare_pending`, and returns
`yoetz.elevated-bootstrap.prepare-result/1` with the service projection. No prepare path reads or
accepts passphrase, reauthentication, provider credential, proof, token, FD content, stdin, env, or
config values.

Assert `approve_elevated` calls `approve_pending` with exact pending ID, danger digest, and
confirmation phrase before invoking any FD read. For `vault_initialize`, it requires only
`passphrase_fd`; for `provider_credential_set`, it requires both `reauth_fd` and `credential_fd`.
Wrong or missing FD sets fail with bounded reasons. Descriptor validation itself remains owned by
the service FD primitive and must reject `0`, `1`, and `2`.

Assert vault initialization validates the passphrase buffer, opens
`HumanCeremonyKind.VAULT_INITIALIZE` with `EmptyVaultTarget(expected_mode="uninitialized")`,
verifies the server preview, sends exactly one `VAULT_INITIALIZE` secret through the confidential
secret client for the matching server binding, waits for a `VaultStateResult`, returns only
`state` and bounded `reason`, closes the client, cancels the session on in-ceremony failure, and
overwrites all local buffers.

Assert provider credential set requires the stored provider binding, validates reauthentication and
credential buffers, opens `HumanCeremonyKind.PROVIDER_CREDENTIAL_SET` with action `set`, verifies
the preview, selects `secret_reauthentication` only when an authorization phase offers it, sends the
reauthentication secret for `PROVIDER_REAUTHENTICATION`, sends the credential for
`PROVIDER_CREDENTIAL`, waits for a `ProviderCredentialResult`, and returns only action, stored
generation, and activation outcome. It must not perform rotate, unlock, privacy, idle-relock,
portable recovery, or any unrelated operation.

Assert accepted approve success clears pending once and returns
`yoetz.elevated-bootstrap.result/1` with pending ID, operation, `completed`, danger digest, and the
bounded structural ceremony result. Assert any exception after accepted approval clears pending
before propagating. Assertion fixtures must prove secret values are never included in returned JSON,
logs, audit events, exception messages, or app-visible structural payloads.

## Errors and edge cases

Cover pending-consent rejection before FD reads, missing passphrase FD, partial provider FD set,
invalid secret validation, confidential-client open/send/wait failures, preview mismatch,
unsupported phase, missing secret for a required purpose, unavailable secret reauthentication,
exhausted phase loop, invalid result type, cancellation failure suppression, close on failure,
buffer overwrite after validator/send exceptions, and clear-pending call ordering. The tests do not
claim malicious same-UID exclusion beyond ADR-015's documented honesty limit.

## Invariants

1. Approve verifies pending ID, danger digest, and exact phrase before any secret read.
2. Operation-specific secrets are read only from inherited FDs and never from forbidden channels.
3. Supported operations are exactly `vault_initialize` and provider credential action `set`.
4. The CLI never mints server bindings, proofs, or reusable authority tokens.
5. Pending state is cleared after accepted approve success and after accepted approve failure.
6. Mutable secret buffers and per-send copies are overwritten best effort on all paths.
7. Returned payloads are structural JSON and contain no secret material.

## Tests

This file is the executable owner for the CLI elevated-bootstrap driver and uses scripted async
sessions, fake FD readers, and structural result fixtures only.

## Open questions

None.

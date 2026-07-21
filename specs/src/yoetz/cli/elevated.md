# src/yoetz/cli/elevated.py — elevated-bootstrap prepare/status/approve CLI driver

**Wave:** D | **ADRs:** ADR-008, ADR-015 | **Imports (spec-tree):** `cli/unlock.md`,
`service/elevated_bootstrap.md`, `service/confidential_client.md`,
`service/confidential_protocol.md`, `protocol/canonical.md`, `protocol/json_types.md` |
**Imported by:** `cli/app.md` elevated-bootstrap commands

## Purpose

Provides the CLI-facing ADR-015 elevated-bootstrap workflow for cloud agents that lack a user-owned
controlling TTY. It prepares nonsecret consent challenges, renders structural status, and, only
after exact founder approval, drives the existing confidential human-control ceremony using secrets
read from inherited file descriptors. It does not add a generic password-FD path or an approval
mode for operations beyond ADR-015.

## Public surface

- `status_elevated() -> dict[str, JsonValue]` returns the service-owned status payload.
- `prepare_elevated(operation, provider_binding=None) -> dict[str, JsonValue]` creates a pending
  consent challenge and returns its structural projection.
- `approve_elevated(pending_id, danger_digest, confirm, passphrase_fd=None, reauth_fd=None,
  credential_fd=None) -> dict[str, JsonValue]` verifies exact pending consent and completes the
  selected operation through inherited FDs.
- Private helpers compute the target digest, complete vault initialization, complete provider
  credential set, drive the confidential ceremony phases, and cancel quietly on failure.

## Behavior

`status_elevated` delegates to the service status payload and returns JSON-ready structural data.
`prepare_elevated` accepts only `vault_initialize` or `provider_credential_set`. For
`vault_initialize`, it binds the pending challenge to the canonical empty-vault target digest. For
`provider_credential_set`, it requires the nonsecret provider binding supplied by the app parser and
binds action `set`, provider/model/profile identifiers, purpose, scope digest, and purpose digest
into the canonical target digest. It then delegates pending creation to the service module and
returns the `yoetz.elevated-bootstrap.prepare-result/1` payload with the agent-safe projection.

`approve_elevated` first calls `approve_pending` with exact `pending_id`, exact `danger_digest`, and
the exact confirmation phrase. No file descriptor is read until that pending consent check accepts.
For `vault_initialize`, it requires `--passphrase-fd` and reads one passphrase from that inherited
descriptor. For `provider_credential_set`, it requires both `--reauth-fd` and `--credential-fd` and
reads one reauthentication passphrase plus one provider credential. The inherited descriptors must
be supplied by the invoking process and cannot be `0`, `1`, or `2`; the module never reads secrets
from stdin, argv values, environment variables, config, MCP, transcript, or chat paste.

After FD reads, the helper validates passphrase and credential buffers with the same service
confidential-protocol validators used by the TTY helper. It opens the existing human-control
ceremony for `VAULT_INITIALIZE` or `PROVIDER_CREDENTIAL_SET`, verifies the server preview against
the locally bound target, selects `secret_reauthentication` only when the provider flow requests
authorization, and sends each mutable secret exactly once through the confidential secret client for
the matching server-required purpose. It never mints a binding, proof, reusable token, or
application authority locally.

On successful completion, `approve_elevated` clears the pending record and returns
`yoetz.elevated-bootstrap.result/1` with pending ID, operation, `completed`, danger digest, and the
bounded structural result. If approval is accepted but FD validation, ceremony setup, phase driving,
secret send, result validation, or operation completion fails, it clears the pending record before
raising. All local secret buffers and any per-send copies are overwritten best effort on every exit.

## Errors and edge cases

Missing operation-specific FDs, invalid reserved FDs, rejected secret bytes, confidential-client
errors, preview mismatch, unsupported phase, unavailable secret reauthentication, wrong or missing
secret purpose, exhausted ceremony steps, cancellation, invalid terminal result type, and all
pending-consent failures return bounded `ElevatedBootstrapError` reasons to the app layer. A failure
after `approve_pending` consumes the pending approval and requires a new prepare. Provider
credential set may only drive action `set`, never rotate, unlock, privacy widening, idle-relock
weakening, portable recovery, or a general yes/yolo operation.

## Invariants

1. Prepare creates only nonsecret pending consent; approve is the only secret-consuming entry point.
2. No secret FD is read until pending ID, danger digest, and exact phrase have all matched.
3. Inherited secret FDs exclude `0`, `1`, and `2`; stdin/argv/env/config/MCP/transcript/chat are
   forbidden secret channels.
4. Supported operations are exactly `vault_initialize` and `provider_credential_set`.
5. Provider credential set uses existing provider reauthentication plus provider-credential YZS1
   purposes and action `set` only.
6. Pending consent is cleared on approve success and on any post-approval failure.
7. The CLI returns only structural JSON and never prints, logs, stores, or audits secret material.

## Tests

- `tests/unit/cli/test_elevated.py` covers target digests, prepare/status projection delegation,
  approve-before-FD-read ordering, operation-specific FD requirements, confidential phase driving,
  clear-on-success/failure, buffer overwrite, and structural result payloads.
- `tests/unit/service/test_elevated_bootstrap.py` covers the pending-store and FD primitive used by
  this module.
- Subprocess boundary tests cover the app command grammar and prove secrets do not cross argv, env,
  stdin, config, MCP, transcript, or normal output.

## Open questions

None.

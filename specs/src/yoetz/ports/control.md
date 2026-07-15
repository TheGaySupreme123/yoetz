# src/yoetz/ports/control.py — ordinary local-service control client port

**Wave:** B | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `protocol/errors.md`,
`protocol/ids.md` | **Imported by:** `service/client.md`, `cli/app.md`, `mcp/server.md`,
`adapters/control/unix_socket.md`

## Purpose

Defines the least-authority boundary used by CLI, MCP, and future ordinary UI surfaces to call the
trusted local service. It carries validated workflow and support envelopes but has no method or
field capable of carrying an unlock secret, recovery secret, provider credential, key handle,
decrypted object handle, service-internal path, or policy-loosening proof. Exact support branches
may carry an explicitly selected, redacted local source/target locator; no generic path field or
implicit cwd exists.

## Public surface

- `class ControlClientPort(Protocol)` with async `connect()`, `call(ControlCallRequest) ->
  ControlResult`, `cancel(ControlCancelRequest) -> None`, `service_status() -> ServiceStatus`,
  `lock() -> ServiceStatus`, `stop() -> ServiceStatus`, and `close()`.
- `enum ControlClientKind` — `cli`, `mcp_bridge`, `ui`.
- `enum ControlMethod` — exactly `start`, `publish_work`, `check`, `respond`, `status`, `receipt`,
  `import_codex_jsonl`, `review`, `backup_preview`, `backup_execute`, `restore_preview`,
  `restore_execute`, `migrate_preview`, `migrate_execute`, `integration_preview`,
  `integration_execute`, `service_status`, `service_lock`, `service_stop`, `privacy_get_setup`,
  `privacy_get_effective`, `privacy_propose_policy`, `privacy_tighten_policy`,
  `privacy_receipts_list`, `privacy_receipts_get`. The decision methods
  `privacy_decide_policy` and `privacy_decide_disclosure` are deliberately absent.
- `@dataclass(frozen=True, slots=True) class ControlCallRequest` — exact call branch fields
  `kind=call`, protocol version, validated `rpc_id`, `service_instance_id`, positive canonical
  `service_generation`, `method`, method-discriminated exact typed `body`, and optional
  `deadline_ms`; it has no target RPC field.
- `@dataclass(frozen=True, slots=True) class ControlCancelRequest` — exact cancel branch fields
  `kind=cancel`, protocol version, validated fresh `rpc_id`, `service_instance_id`, positive
  canonical `service_generation`, and distinct `target_rpc_id`; it has no method, body, deadline,
  or cancellation-token field.
- `ControlRequest = ControlCallRequest | ControlCancelRequest`, a closed tagged union matching
  `service/control-request-1.0.0` exactly. Cancellation is the second wire branch, not metadata on
  a call.
- `@dataclass(frozen=True, slots=True) class ControlResult` — `rpc_id`, outcome, exact typed result
  or sanitized control error, and service instance/generation identifiers.
- `enum ServiceState` — `starting`, `locked`, `unlocking`, `ready`, `draining`, `failed`.
- `@dataclass(frozen=True, slots=True) class ServiceStatus` — protocol/service versions, state,
  bounded state reason, service instance/generation, vault mode classification, capabilities, and
  session-event monitor status. It contains no PID, path, username, key locator, provider
  credential status, or user content.
- `class ControlError(Exception)` — one bounded reason from `service_unavailable`,
  `peer_untrusted`, `protocol_mismatch`, `frame_invalid`, `frame_too_large`, `request_cancelled`,
  `request_timeout`, `vault_locked`, `service_draining`, `method_forbidden`, `internal_error`.
  Ready-result local-audit reservation failure uses
  `privacy_projection_unavailable` (retryable, no content result serialized).
  The wire-only reason `service_generation_changed` is also accepted from a parsed control result
  and maps at the public boundary to `SERVICE_UNAVAILABLE`; it is never exposed as a new public
  error code.

The five frozen schemas are `service/control-hello-1.0.0`,
`service/control-hello-result-1.0.0`, `service/control-request-1.0.0`,
`service/control-result-1.0.0`, and `service/service-status-1.0.0`.

## Behavior

`connect` establishes a same-UID authenticated local connection and completes the version/client-
kind handshake before any request. `call` validates the method body against its existing exact
closed envelope branch (an operation `$ref` or an inline support `$def`) before serialization and
validates the matching closed result branch after receipt. The
control envelope adds transport correlation and service generation; it never wraps or changes the
operation's own request ID/idempotency meaning.

The client may retry a connection failure only by resending the identical `ControlRequest` and
operation request ID. Cancellation is an explicit structural control frame; disconnect alone is
not proof that the service cancelled a committing operation. `service_status` is available while
locked. `lock` and `stop` are allowed for CLI/future trusted local UI clients and denied to the MCP
bridge. The MCP bridge is advertised and admitted for exactly the six workflow methods; import/
review (including JSONL bytes), maintenance, integration, lifecycle and privacy support calls are
denied even when their body is schema-valid. No method can unlock, initialize, migrate vault mode,
submit credentials, or loosen privacy policy.

Ordinary CLI/UI clients may inspect setup/effective policy, submit an inert proposal, or apply a
mathematically proven tightening. A proposal returns its exact digest/diff and remains uncommitted.
Approval/widening and per-request disclosure decisions use `service/human_control.md`; no ordinary
result carries decision authority or a reusable proof.

Ordinary CLI/UI may also list/get bounded structural privacy receipts. Those two bodies are
plaintext-free, audit-read-only, and explicitly projection/audit-exempt to avoid creating a receipt
for inspecting receipts; their authenticated snapshot cursor prevents pagination drift. MCP cannot
invoke them, and they cannot dereference proposal/object content.

## Errors and edge cases

- Missing endpoint/refused connection is `service_unavailable`; clients never create a direct
  application fallback.
- `locked` rejects every method except status, lock (idempotent), and stop with `vault_locked`.
- `draining` accepts status only; new work is `service_draining`.
- Unknown method, extra field, wrong schema version, duplicate key, float/noncanonical integer, or
  oversized frame is rejected before dispatch and never reflected verbatim.
- A result with a mismatched RPC ID, service generation, method result schema, or request binding
  closes the connection and fails closed.
- `service_generation_changed` closes the connection before retry and maps to the public
  `SERVICE_UNAVAILABLE` envelope; the client does not leak the control token into workflow results.
- `privacy_projection_unavailable` maps to retryable public `SERVICE_UNAVAILABLE`; identical
  operation replay may recover the committed internal result and retry its local projection. It
  never includes the internal result, receipt ID, policy detail, or candidate bytes.
- `human_authority_unavailable` is valid only for locked/uninitialized setup rejected before
  keyring mutation or ready/os-keyring local admission with `external_provider` omitted. It reveals
  no policy, credential, or backend detail.

## Invariants

1. Secret-bearing types are absent from this module and impossible in every frozen control schema.
2. The MCP client kind can call exactly the six workflow methods; it cannot call import/review,
   maintenance, integration, lock, stop, secret ingress, privacy setup/control, policy-loosening,
   or privacy-receipt-inspection methods.
3. A control response never asserts whether an operation committed merely because a connection
   ended; durable operation replay resolves ambiguity.
4. Same service behavior is observed through CLI, MCP, and UI for the same operation envelope.
5. Fixed control errors and the closed structural lifecycle results (hello, status, lock, stop) need
   no local-disclosure receipt; every ready content-capable result does.

## Tests

- `tests/unit/service/test_control_protocol.py` proves the method registry, schema bindings, and
  absence of secret-shaped fields.
- `tests/integration/service/test_local_control_channel.py` runs every method/client-kind/state
  matrix against the Unix-socket adapter.
- `tests/conformance/surfaces/test_cli_mcp_parity.py` proves equal public results through both
  bridges.

## Open questions

None.

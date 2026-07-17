# src/yoetz/service/client.py — ordinary local-service client used by CLI and MCP

**Wave:** C/D | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `ports/control.md`,
`service/control_protocol.md`, `adapters/control/unix_socket.md`, `protocol/models.md` |
**Imported by:** `cli/app.md`, `mcp/server.md`, future ordinary UI adapter

## Purpose

Provides one client implementation for all normal local surfaces. It finds and authenticates the
per-user service, converts strict operation/support values to frozen control envelopes, and returns
the exact typed result. It owns no configuration merge, application facade, provider, storage,
vault, or direct-execution fallback.

## Public surface

- `class ServiceClient(ControlClientPort)` implementing the port.
- `async connect_service(client_kind: ControlClientKind) -> ServiceClient`.
- Six exact async workflow methods matching `Application`: `start`, `publish_work`, `check`,
  `respond`, `status`, `receipt`.
- Exact ordinary support methods `import_codex_jsonl`, `review`, `backup_preview`, `backup_execute`,
  `restore_preview`, `restore_execute`, `migrate_preview`, `migrate_execute`,
  `integration_preview`, `integration_execute`, `privacy_get_setup`, `privacy_get_effective`,
  `privacy_propose_policy`, `privacy_tighten_policy`, `privacy_receipts_list`, and
  `privacy_receipts_get`. These names are the wire tokens; the client performs no alias mapping.
  The two receipt-inspection methods are CLI/UI-only and neither is admitted for `mcp_bridge`.
  Their exact signatures are
  `async privacy_receipts_list(ListPrivacyReceiptsRequest) -> PrivacyReceiptPage` and
  `async privacy_receipts_get(GetPrivacyReceiptRequest) -> PrivacyReceiptGetResult`, where the
  latter is the exact closed control result union `found(PrivacyReceiptView)|not_found`, never a
  nullable wire value.
- Structural lifecycle methods `service_status`, `lock`, `stop`, available only under the client-
  kind rules.
- `async close()` — idempotent, cancels/awaits local receiver tasks without implying remote
  operation cancellation.

No constructor or method accepts a socket path, data directory, key locator, password, credential,
environment mapping, provider client, `Application`, or adapter factory.

## Behavior

`connect_service` derives the endpoint only through the verified platform runtime path, asks the
Unix-socket adapter to authenticate the service peer as the current effective UID, then performs
the frozen handshake. It never starts a service automatically. A missing endpoint is a bounded
`service_unavailable` result with human guidance owned by the surface renderer.

Each operation validates its already typed body, allocates a fresh transport RPC ID, preserves the
operation request ID, sends one control request, and validates the exact result. Safe connection
reuse is allowed; reconnect never changes client kind or service generation silently. If the
connection is lost, the caller may retry only with the identical operation request. The client
does not infer commit/cancel state from EOF.

Cancellation sends the exact one-way `ControlCancelRequest` branch with its own fresh RPC ID and
the target call RPC ID. It never waits for or fabricates a cancel acknowledgement; only the
original call's eventual reviewed result or `request_cancelled` control result resolves outcome.

The MCP bridge uses `client_kind=mcp_bridge`, so the service advertises and accepts exactly the six
workflow methods. It rejects import/review (including raw JSONL source), maintenance, integration,
lifecycle, privacy-control/receipt-inspection and all confidential ingress even if MCP code tries a schema-valid
support branch as a raw method. CLI uses `client_kind=cli`. Neither kind can unlock, initialize the
vault, store provider credentials, mint human authorization, or loosen privacy policy through this
class.

Privacy receipt inspection returns only the structural receipt view/page and snapshot cursor from
the server's internal `PrivacyAuditPort.list_receipts`/`get_receipt` methods; those internal names
are not exposed as wire aliases. Inspection never fetches an encrypted proposal object or creates
a new local-disclosure receipt for the inspection itself.
The server maps the internal page fields one-to-one to `PrivacyReceiptPage`; it maps an internal
`PrivacyReceiptView | None` to the client's closed `found|not_found` result. The client never
collapses `not_found` into an empty view or exposes the internal optional directly.

## Errors and edge cases

- Service absent → `ControlError(service_unavailable)`; no subprocess spawn/direct app fallback.
- Service locked → structural status remains available; workflow/support calls become the
  sanitized `vault_locked` mapping without a prompt or secret field.
- Service draining → bounded `service_draining`. Wire `service_generation_changed` closes the stale
  session and maps to public `SERVICE_UNAVAILABLE`; identical durable request replay resolves an
  ambiguous workflow result after reconnect.
- Ready-result audit reservation failure → bounded retryable
  `privacy_projection_unavailable`, mapped to public `SERVICE_UNAVAILABLE`; the client receives no
  unprojected body and may replay only the identical operation request.
- A server peer with wrong UID, unsafe endpoint type/owner/mode, or protocol mismatch is rejected
  before request bytes leave.
- `fork()` after connect invalidates the inherited client; child use fails closed.

## Invariants

1. CLI and MCP share this exact client; neither imports service daemon/application composition.
2. Client memory contains request/result content but no vault key, unlock/recovery secret, or
   provider credential.
3. Endpoint discovery and peer identity cannot be overridden by repository/user task content.
4. Connection loss never changes idempotency or creates a second execution path.
5. MCP cannot send import source bytes or any other support body through this client.

## Tests

- `tests/unit/service/test_client.py` covers request/result conversion, client-kind method limits,
  reconnect, cancellation, and no direct-runtime constructor surface.
- `tests/integration/service/test_daemon_clients.py` runs concurrent CLI and MCP clients through one
  service and compares results.
- `tests/packaging/test_service_boundary_imports.py` asserts the client import graph excludes
  SQLite, cryptography, keyring, provider SDK, and application composition.

## Open questions

None.

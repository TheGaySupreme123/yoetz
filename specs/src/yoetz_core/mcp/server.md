# src/yoetz_core/mcp/server.py — stdio MCP-to-local-service bridge

**Wave:** D | **ADRs:** ADR-001, ADR-005, ADR-007, ADR-008 | **Imports (spec-tree):**
`mcp/errors.md`, `mcp/summaries.md`, `adapters/mcp_stdio.md`, `protocol/models.md`,
`service/client.md` | **Imported by:** `cli/app.md`, MCP subprocess tests

## Purpose

Binds the stable MCP v1 stdio server to one ordinary `ServiceClient`. It owns strict MCP parsing,
tool schemas, result validation, fallback, and stdout purity, but no application/runtime/storage/
vault/provider state. MCP is a bridge, never the trusted service or unlock surface.

## Public surface

- Low-level MCP `server`, `list_tools`, six `dispatch_<op>`, `call_tool`, result/fallback helpers,
  and `main`.
- Frozen `BridgeRuntime` holding only `ServiceClient(client_kind=mcp_bridge)` and public schemas.
- Exactly six workflow tools. No service start/status/lock/stop/unlock, secret input, provider
  credential, recovery, setup, or privacy-policy mutation tool.

## Behavior

Lifespan connects to the already-running same-UID service, loads/generates six tool descriptors,
validates shared fallback, and closes the client on shutdown. It never constructs `Application`,
loads Yoetz config/user state, imports keyring/SQLite/provider SDK, starts a daemon, or prompts.

Each dispatcher strictly validates MCP arguments, converts to the exact public request, calls the
matching `ServiceClient` method, validates the public envelope, and returns structured content plus
a weaker summary. Service absent, locked, draining, or generation change becomes one sanitized
structured tool error with bounded guidance; no secret field or direct-execution fallback exists.
Configured semantic failure preserves the service's deterministic `incomplete_check` result,
including the exact validated semantic status/reason pair and no semantic findings.

The existing bounded stdio transport, nested fallback, cancellation, output-schema validation,
and stdout-only-protocol rules remain binding. Client cancellation is forwarded structurally, but
EOF/disconnect never asserts remote commit cancellation; request-ID replay resolves ambiguity.

## Errors and edge cases

- Invalid request/unknown tool remains structured and sanitized.
- Missing service does not launch it; locked does not offer an MCP unlock argument.
- Unexpected bridge/client/SDK errors use prevalidated `INTERNAL_ERROR`; no raw exception/request.
- Service reconnect requires fresh same-UID handshake/generation and identical operation request.
- Clean EOF closes the bridge only; persistent service remains alive.

## Invariants

1. MCP exposes exactly six operations and cannot access confidential/lifecycle methods.
2. MCP process never owns key/credential/decrypted state/application/storage/provider capability.
3. Every input/result validates at MCP and service-control boundaries.
4. Stdout contains protocol frames only; all exceptions are sanitized.
5. Closing MCP never closes the service or shared task runtime.

## Tests

- `tests/conformance/surfaces/test_mcp_contract_matrix.py` covers registry/dispatch/fallback and
  absence of service/secret tools.
- `tests/subprocess/test_mcp_service_bridge.py` covers absent/locked/ready/reconnect/response loss.
- `tests/subprocess/test_mcp_stdout_purity.py` covers protocol-only stdout.
- `tests/packaging/test_service_boundary_imports.py` proves bridge imports exclude trusted modules.

## Open questions

None.

# src/yoetz/mcp/server.py — stdio MCP-to-local-service bridge

**Wave:** D | **ADRs:** ADR-001, ADR-005, ADR-007, ADR-008, ADR-010 | **Imports (spec-tree):**
`mcp/errors.md`, `mcp/summaries.md`, `mcp/descriptors.md`, `mcp/resources.md`,
`adapters/mcp_stdio.md`, `protocol/models.md`, `service/client.md` | **Imported by:** `cli/app.md`,
MCP subprocess tests

## Purpose

Binds the stable MCP v1 stdio server to one ordinary `ServiceClient`. It owns strict MCP parsing,
tool schemas, result validation, fallback, and stdout purity, but no application/runtime/storage/
vault/provider state. MCP is a bridge, never the trusted service or unlock surface.

It is also Yoetz's zero-integration baseline: any host, profiled or not, reaches the same six
operations and the same guidance here, with no skill installed and nothing to configure (ADR-010).

## Public surface

- Low-level MCP `server`, `list_tools`, six `dispatch_<op>`, `call_tool`, result/fallback helpers,
  `list_resources`/`read_resource` delegating to `mcp/resources.md`, and `main`.
- Frozen `BridgeRuntime` holding public schemas, the frozen descriptor table, the frozen guidance
  resource registry, and one private lazy `ServiceClient(client_kind=mcp_bridge)` slot. The slot is
  initially empty and is touched only by tool dispatch, never resource dispatch.
- `async ensure_service_client(runtime) -> ServiceClient` — lazily connect/reconnect the one MCP
  client for a tool call, starting the fixed service process when absent, and convert
  locked/draining/generation-change outcomes to the bounded
  bridge error contract without affecting static resources.
- Exactly six workflow tools. No service start/status/lock/stop/unlock, secret input, provider
  credential, recovery, setup, or privacy-policy mutation tool.
- Declared capabilities are exactly tools and resources, plus the `instructions` string. No prompts,
  no sampling, no roots, no completions, and no subscribe/listChanged on resources: the guidance set
  is frozen at startup and cannot change while the process lives.

## Behavior

Lifespan loads the frozen six tool descriptors and the `instructions` string from
`mcp/descriptors.md`, builds the guidance resource registry, validates shared fallback, and leaves
the service-client slot empty. It does not require the service to exist. On shutdown it closes the
slot only if a tool call created it. It never constructs `Application`, loads Yoetz config/user
state, imports keyring/SQLite/provider SDK, starts a daemon, or prompts.

Descriptor, instruction, and guidance bytes are verified against the resource manifest before the
server serves anything; verification failure is fatal to startup. The server never composes,
summarizes, or falls back to a literal for text an agent reads.

Tools and resources are different surfaces with different rules, and conflating them is the mistake
this file must not make. Tools reach the service and carry user content, so every result passes the
disclosure fence. Resources are static reviewed product documents that carry none, depend on no
service state, and are therefore served while the service is absent, locked, or draining — exactly
when an agent most needs to know that Yoetz is unavailable rather than to invent a session. No
resource read reaches `ServiceClient`.

Each tool dispatcher strictly validates MCP arguments, converts to the exact public request, calls
`ensure_service_client`, invokes the matching method, validates the public envelope, and returns
structured content plus a weaker summary. A failed lazy connect clears the slot; a generation change
closes/clears it before any identical-request reconnect. Service absent, locked, draining, or
generation change becomes one sanitized structured tool error with bounded guidance; the bridge
stays alive and its static resources remain available. No secret field or direct-execution fallback
exists.
Configured semantic failure preserves the service's deterministic `incomplete_check` result,
including the exact validated semantic status/reason pair and no semantic findings.

The ordinary service path returns workflow failures as `ok:false` public Result models. As defense
in depth, if a `PublicOperationError` escapes the client boundary, `_dispatch` binds a correlation
ID when unbound and maps it through `tool_error_envelope` into the same structured tool failure
shape—preserving codes such as `EVENT_INVALID`—rather than collapsing them to `INTERNAL_ERROR`.

The existing bounded stdio transport, nested fallback, cancellation, output-schema validation,
and stdout-only-protocol rules remain binding. Client cancellation is forwarded structurally, but
EOF/disconnect never asserts remote commit cancellation; request-ID replay resolves ambiguity.

Protocol-version negotiation is owned by the pinned MCP SDK session: a mutually supported requested
version is echoed; an unknown requested version is answered with the server's latest supported
version; the client decides whether to disconnect. Structurally malformed initialize requests still
fail through ordinary protocol errors. Yoetz does not pre-reject unknown versions before SDK
negotiation.

An unregistered `tools/call` name is answered as a sanitized JSON-RPC `INVALID_PARAMS` error whose
message never echoes the caller-controlled name. The bridge owns this path so the SDK's tool-cache
warning cannot interpolate that name onto stderr. Input and business validation failures for
registered tools remain structured tool results.

## Errors and edge cases

- Invalid request on a registered tool remains a structured sanitized tool result. An unknown tool
  name is a sanitized JSON-RPC error and never a tool execution result.
- Missing service triggers only the reviewed fixed on-demand launcher; locked does not offer an MCP
  unlock argument.
- Missing service never prevents MCP initialization or static guidance list/read; only a tool call
  attempts the lazy service connection.
- Unexpected bridge/client/SDK errors use prevalidated `INTERNAL_ERROR`; no raw exception/request.
  Known `PublicOperationError` application failures are not unexpected: they use
  `tool_error_envelope` / the service's `ok:false` body and keep their public code.
- Service reconnect requires fresh same-UID handshake/generation and identical operation request.
- Clean EOF closes the bridge only; persistent service remains alive.
- A missing or digest-mismatched descriptor, instruction, or guidance resource fails startup. The
  server never serves unverified agent-read text and never starts with empty `instructions`.
- An unknown or traversing resource URI is a bounded structural error and never a filesystem read.
- A host that ignores `instructions` or does not support resources is still supported; it simply
  operates with less guidance, which is why tier 0 carries every harm-avoiding rule on its own.

## Invariants

1. MCP exposes exactly six operations and cannot access confidential/lifecycle methods.
2. MCP process never owns key/credential/decrypted state/application/storage/provider capability.
3. Every input/result validates at MCP and service-control boundaries.
4. Stdout contains protocol frames only; all exceptions are sanitized.
5. Closing MCP never closes the service or shared task runtime.
6. Resources are static product documents only: they reach no service, carry no user content, create
   no disclosure receipt, and add no seventh operation.
7. Every agent-read string is verified reviewed bytes, never runtime-composed.
8. Service absence may trigger one bounded on-demand start for a tool call but cannot affect static
   resource startup or reads.

## Tests

- `tests/conformance/surfaces/test_mcp_contract_matrix.py` covers registry/dispatch/fallback,
  absence of service/secret tools, the exact declared capability set, and that resources add no
  operation.
- `tests/subprocess/test_mcp_service_bridge.py` covers absent/locked/ready/reconnect/response loss,
  that resource list/read succeed in every one of those service states, and that escaped
  `PublicOperationError` (for example `EVENT_INVALID` / `unsorted_set_field`) stays structured rather
  than becoming `INTERNAL_ERROR`.
- `tests/subprocess/test_mcp_initialize_and_tools.py` covers the negotiated `instructions` bytes and
  fatal startup on a corrupted guidance resource.
- `tests/subprocess/test_mcp_stdout_purity.py` covers protocol-only stdout.
- `tests/packaging/test_service_boundary_imports.py` proves bridge imports exclude trusted modules.
- `tests/capability/test_mcp_protocol_and_sdk.py` covers pinned SDK/protocol identity probes.
- `tests/capability/test_mcp_gate1_protocol_conformance.py` covers Gate-1 protocol conformance and
  MCP conduit behavior (tools/call, resources, framing); it says nothing about model activation.

## Open questions

None.

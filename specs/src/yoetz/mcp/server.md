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
- Frozen `BridgeRuntime` holding only `ServiceClient(client_kind=mcp_bridge)`, public schemas, the
  frozen descriptor table, and the frozen guidance resource registry.
- Exactly six workflow tools. No service start/status/lock/stop/unlock, secret input, provider
  credential, recovery, setup, or privacy-policy mutation tool.
- Declared capabilities are exactly tools and resources, plus the `instructions` string. No prompts,
  no sampling, no roots, no completions, and no subscribe/listChanged on resources: the guidance set
  is frozen at startup and cannot change while the process lives.

## Behavior

Lifespan connects to the already-running same-UID service, loads the frozen six tool descriptors and
the `instructions` string from `mcp/descriptors.md`, builds the guidance resource registry, validates
shared fallback, and closes the client on shutdown. It never constructs `Application`, loads Yoetz
config/user state, imports keyring/SQLite/provider SDK, starts a daemon, or prompts.

Descriptor, instruction, and guidance bytes are verified against the resource manifest before the
server serves anything; verification failure is fatal to startup. The server never composes,
summarizes, or falls back to a literal for text an agent reads.

Tools and resources are different surfaces with different rules, and conflating them is the mistake
this file must not make. Tools reach the service and carry user content, so every result passes the
disclosure fence. Resources are static reviewed product documents that carry none, depend on no
service state, and are therefore served while the service is absent, locked, or draining — exactly
when an agent most needs to know that Yoetz is unavailable rather than to invent a session. No
resource read reaches `ServiceClient`.

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

## Tests

- `tests/conformance/surfaces/test_mcp_contract_matrix.py` covers registry/dispatch/fallback,
  absence of service/secret tools, the exact declared capability set, and that resources add no
  operation.
- `tests/subprocess/test_mcp_service_bridge.py` covers absent/locked/ready/reconnect/response loss,
  and that resource list/read succeed in every one of those service states.
- `tests/subprocess/test_mcp_initialize_and_tools.py` covers the negotiated `instructions` bytes and
  fatal startup on a corrupted guidance resource.
- `tests/subprocess/test_mcp_stdout_purity.py` covers protocol-only stdout.
- `tests/packaging/test_service_boundary_imports.py` proves bridge imports exclude trusted modules.
- `tests/capability/test_mcp_protocol_and_sdk.py` covers an unprofiled host completing the workflow
  with no installed skill.

## Open questions

None.

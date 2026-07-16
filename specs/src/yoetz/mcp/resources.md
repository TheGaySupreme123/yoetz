# src/yoetz/mcp/resources.py — read-only guidance resource registry

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`resources/guidance/agent-instructions.md`, `resources/guidance/workflow.md`,
`resources/guidance/publication-policy.md`, `resources/guidance/coverage-and-receipts.md`,
`resources/manifest.json.md` | **Imported by:** `mcp/server.md`, MCP contract and capability tests

## Purpose

Serve the harness-neutral guidance documents as MCP resources, so an agent that wants more than the
`instructions` floor can fetch exactly the section it needs, when it needs it.

This is the on-demand tier of the delivery model in `guidance/README.md`, and it is what makes the
guidance reachable without a first-party integration. Codex reaches these documents as installed
files; every other host reaches these same bytes here. Without this registry, an unprofiled host
would get six tools and a 2 KiB floor, and the deep material — the event-family cheat sheet, the
coverage rules, the excerpt boundary — would exist only for Codex (ADR-010).

It serves static product documents. It is not a second read path into the ledger: it exposes no
task, session, projection, finding, receipt, or user content, and no tool result is reachable
through it.

## Public surface

- `GUIDANCE_RESOURCES` — frozen tuple of the four registered resources, in stable order.
- `list_resources()` — MCP `resources/list`; returns URI, name, title, description, and MIME type.
- `read_resource(uri)` — MCP `resources/read`; returns verified bytes for exactly one registered URI.

Registered URIs are exactly:

```text
yoetz://guidance/agent-instructions.md
yoetz://guidance/workflow.md
yoetz://guidance/publication-policy.md
yoetz://guidance/coverage-and-receipts.md
```

## Behavior

The registry is closed and built at startup from the packaged `guidance/` resources. Each member's
size and SHA-256 are verified against the resource manifest before registration; a failure is fatal
to startup rather than degraded, for the same reason it is fatal in `mcp/descriptors.md`.

`read_resource` accepts only an exact registered URI. It resolves no path, accepts no template,
traversal, glob, or relative segment, and reads only through `importlib.resources`. A URI is a
lookup key into a frozen table, never a filesystem instruction — a resource reader that takes a path
is a file-read primitive, and this server must not have one.

Resource text is identical to the packaged bytes and to every harness's installed copy. The server
does not summarize, template, paginate, or annotate the documents at read time.

`list_resources` is available whenever the server is initialized, including when the service is
absent or the vault is locked. Guidance is static product text and depends on no service state, so
an agent can learn how to use Yoetz correctly before Yoetz is ready — which is precisely when it
most needs to know that Yoetz is unavailable rather than to invent a session.

### Why this is not a disclosure sink

Serving guidance discloses no ledger, task, projection, provider, or user content. It is reviewed
product text identical for every installation, so it carries no information about this user, this
workspace, or this task. It is therefore not a `LocalDisclosureSink`, requires no
`agent_context_categories` grant, and creates no `LocalDisclosureReceipt`. Adding any content that
varies by installation would change that analysis and is forbidden here: this registry is for static
documents only.

## Errors and edge cases

- An unknown, malformed, templated, or traversing URI is a bounded structural resource error. It is
  never a filesystem read, and it never reaches package-resource lookup.
- A missing or digest-mismatched guidance member fails startup; a partial guidance set is never
  served, because guidance that silently loses its forbidden-content rules is worse than no
  guidance.
- A host that does not support resources is fully supported: it still receives
  `guidance/agent-instructions.md` as the initialize `instructions` string, which is why tier 0 must
  carry every harm-avoiding rule on its own.
- Resource reads are unaffected by service absence, `vault_locked`, or draining state.
- No resource read creates a ledger event, an operation, a disclosure receipt, or any durable
  effect.

## Invariants

1. The registry is closed, read-only, and contains only static reviewed product documents.
2. A URI is a key into a frozen table and never a path.
3. Served bytes equal packaged bytes and every installed harness copy.
4. Resources expose no ledger, task, projection, or user content and are not a disclosure sink.
5. Resource reads have no durable effect and create no receipt.
6. Guidance is reachable without any first-party integration.

## Tests

- `tests/conformance/surfaces/test_mcp_contract_matrix.py` — exact registered URI set and list
  shape; unknown/templated/traversing URIs are bounded structural errors with no filesystem access.
- `tests/subprocess/test_mcp_service_bridge.py` — resources list and read succeed while the service
  is absent, locked, and draining.
- `tests/capability/test_mcp_protocol_and_sdk.py` — an unprofiled host discovers and reads the
  guidance over the pinned SDK and completes the workflow with no installed skill.
- `tests/conformance/privacy/test_never_send_scope_and_channels.py` — no resource read produces a
  disclosure receipt or admits any task/user content.
- `tests/packaging/test_resource_byte_parity.py` — served bytes equal packaged and installed bytes.

## Open questions

None.

MCP prompts are deferred to v0.2; v0.1 ships tools, resources, and instructions only.

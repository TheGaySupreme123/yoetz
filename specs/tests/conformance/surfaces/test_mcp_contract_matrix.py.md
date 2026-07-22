# tests/conformance/surfaces/test_mcp_contract_matrix.py — MCP contract matrix

**Wave:** D | **ADRs:** ADR-003, ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`src/yoetz/mcp/server.md`, `src/yoetz/mcp/errors.md`, `src/yoetz/mcp/descriptors.md`,
`src/yoetz/mcp/resources.md`
**Imported by:** conformance surface tests

## Purpose

Prove MCP initialization, tool discovery, agent-read text, guidance resources, structured content,
and fallback errors match the public contract.

## Public surface

- `test_initialize_and_tools_list_contract` — version negotiation, declared capabilities, and tools
  list are exact.
- `test_tool_call_parity_and_isError_mapping` — tool calls map to the right public result/error.
- `test_fallback_error_object_is_admitted` — the last-resort error object is accepted.
- `test_public_error_and_validation_summaries_are_sanitized` — public error envelopes expose only
  allowlisted paths/reason codes and their text never exceeds structured content.
- `test_forbidden_client_id_projects_parent_path_in_safe_details` — invented `client.id`
  (`extra_forbidden` on `("client", "id")`) projects usable `/client` in safe details without
  echoing the untrusted leaf or allowlisting a generic `id` segment.
- `test_unknown_tool_message_is_sanitized` — unknown-tool public messages never echo the raw name;
  over stdio, unregistered tools/call names are JSON-RPC errors (see subprocess malicious-name
  coverage).
- `test_descriptor_text_is_frozen_and_honest` — the six descriptors, their order, and their
  annotations are exact, and every description plus the instructions text passes the wording lint.
- `test_guidance_resources_are_exact_and_static` — the four registered URIs are exact, bytes equal
  the packaged resources, and no resource read reaches the service or creates a receipt.
- `test_resource_uri_is_a_key_not_a_path` — unknown, templated, traversing, and relative URIs are
  bounded structural errors with no filesystem access.

## Behavior

The test asserts:

- tools/list reports exactly the six operation tools, in frozen order, and resources add none;
- declared capabilities are exactly tools plus resources with the `instructions` string, and no
  prompts, sampling, roots, completions, or resource subscribe/listChanged;
- `readOnlyHint` is true for exactly `status` (not `receipt`, which records a ledger event), every
  descriptor carries `idempotentHint=true`, and no descriptor claims `destructiveHint`;
- the negotiated `instructions` bytes equal the packaged `guidance/agent-instructions.md` bytes;
- no descriptor or instruction string says "verified", "proved", "authenticated", or "complete"
  without stating the exact sufficient coverage in the same sentence;
- structured output and `isError` are exact;
- fallback error envelopes are admissible before stdin is accepted;
- validation and transport noise remain bounded;
- every public error branch has an admissible structured envelope and a deterministic sanitized
  summary containing only allowlisted field paths and bounded reason codes;
- an invented wrapper field such as `client.id` yields `extra_forbidden` with projected field
  `/client` (parent path), never a field-less `INVALID_REQUEST` and never an echoed untrusted leaf;
- unknown tool, unknown method, malformed request, and valid application error remain four distinct
  outcomes with no tool-side mutation for the first three.

## Errors and edge cases

- A fallback that is not admitted by the schema fails.
- A validation summary that echoes rejected values, secrets, paths outside the allowlist, traceback,
  or exception text fails.
- An unknown tool that reaches application dispatch or is reported as a transport parse failure
  fails.
- A descriptor or instruction string that is composed at runtime, varies between installations, or
  differs from the packaged bytes fails.
- A resource URI that reaches the filesystem, resolves a path, or escapes the frozen table fails.
- A resource read that reaches `ServiceClient`, admits task/user content, or creates a disclosure
  receipt fails.
- A seventh tool, a prompts capability, or a resource subscribe capability fails.

## Invariants

1. MCP surface is frozen.
2. Fallback error shape is explicit.
3. Tool discovery is exact.
4. Error summaries are bounded projections of structured public errors.
5. Unknown-tool handling is side-effect free and protocol-distinct.
6. Every agent-read string is verified reviewed bytes and passes the honesty lint.
7. Resources are static product documents: no service reach, no user content, no receipt, no
   seventh operation.

## Tests

- `tests/conformance/surfaces/test_mcp_contract_matrix.py`

## Open questions

None.

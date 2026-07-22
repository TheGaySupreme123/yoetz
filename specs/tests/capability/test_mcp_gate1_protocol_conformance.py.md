# tests/capability/test_mcp_gate1_protocol_conformance.py — Gate 1 MCP protocol conformance

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** capability evidence, MCP
server/descriptors/resources, pinned MCP SDK | **Imported by:** release capability policy

## Purpose

Prove installed `yoetz mcp serve` protocol conformance and MCP dispatch behavior through the pinned
Python MCP SDK client and raw JSON-RPC framing. This gate says nothing about model activation.

## Public surface

Required observation cases enrolled in `release/capability-policy.json` under family
`mcp_protocol_conformance`, external version `1.28.1`, platform `linux_x86_64`:

- `mcp_initialize_all_supported_versions`
- `mcp_unknown_version_fallback`
- `mcp_capability_declaration_exact`
- `mcp_tools_list_exact_six`
- `mcp_resources_list_read_all`
- `mcp_tools_call_all_six_dispatch`
- `mcp_unknown_tool_sanitized`
- `mcp_malformed_framing`
- `mcp_idempotent_retry_stable`
- `mcp_cancellation_eof_clean`
- `mcp_pending_responses_flush_on_eof`
- `mcp_stdout_purity`

## Behavior

Drive `uv run yoetz mcp serve` via `mcp.client.stdio` / `ClientSession` for capability declaration,
tools/list (exact six descriptors), resources/list+read (digest agreement with packaged bytes), and
tools/call for all six names using a request-ID-only input that each real handler rejects at its
common structured `INVALID_REQUEST` boundary. Service conduit behavior remains owned by the
dedicated integration suite and is not inferred from Gate 1.
Use raw frames for every `SUPPORTED_PROTOCOL_VERSIONS` negotiation, unknown-version fallback,
unknown/malicious tool names, malformed framing, an actual cancellation notification followed by
EOF, pending-response flush before EOF shutdown, and stdout purity. Idempotent
retry compares structural identity for the same `request_id`.

Each case emits `CapabilityEvidence` into `YOETZ_CAPABILITY_EVIDENCE_DIR` when set.

## Errors and edge cases

- Non-`linux_x86_64` platforms skip rather than invent a policy cell pass.
- Unknown tools are JSON-RPC errors, never tool results that echo the raw name.

## Invariants

1. Protocol conformance ≠ model activation.
2. Structured dispatch validation is not service-conduit evidence.
3. Required case IDs match `release/capability-policy.json` exactly.

## Tests

This file is the Gate-1 suite.

## Open questions

None.

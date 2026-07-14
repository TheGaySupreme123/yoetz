# tests/capability/test_mcp_protocol_and_sdk.py — pinned MCP SDK/protocol empirical gate

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** capability evidence, MCP server/
stdio/schema specs | **Imported by:** MCP support matrix

## Purpose

Prove the installed candidate behaves with the exact pinned MCP SDK and negotiated protocol,
including the places where Yoetz deliberately owns validation/framing rather than SDK defaults.

## Public surface

Cells cover `mcp==1.28.1`, protocol `2025-11-25`, each explicitly supported fallback, and negative
candidate SDK/protocol versions. Scenarios: initialize/list/call, direct structured output,
validation ownership, output schema, `isError`, malformed/null-ID, EOF, cancellation, stdout cap.

## Behavior

Install exact SDK by locked hash in clean environment and record distribution/version. Drive both
the SDK low-level client/inspector-style path and independent raw frames. Assert negotiation/tool
schemas/results, SDK validation disabled where Yoetz strict validator owns it, fixed application vs
JSON-RPC errors, cancellation propagation, and bounded stdio rather than SDK default line reader.

Run adversarial inputs that SDK might coerce or report verbosely; Yoetz rejects with its safe fixed
envelope. The malformed unrecoverable-ID transcript matches frozen vector. Probe stable MCP v2 only
in a denied/experimental cell until explicitly adopted; prerelease never supports release claim.

## Errors and edge cases

- Installed version/distribution/hash mismatch invalidates evidence.
- An SDK API import succeeding is not capability proof.
- Raw SDK exception/validation detail cannot enter public result/evidence.
- Fallback protocol is supported only after complete critical matrix.

## Invariants

1. MCP support names exact SDK/protocol cells.
2. Yoetz remains authority for strict validation, privacy errors, and bounded stdio.
3. Structured output and `isError` agree with public contract.
4. Unknown/prerelease versions are untested/denied, not inferred.

## Tests

Emit evidence for SDK/protocol identity, all required case IDs, raw transcript digest, and outcome;
run on every advertised platform artifact.

## Open questions

None.

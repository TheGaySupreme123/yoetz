# src/yoetz_core/mcp/summaries.py — compact safe MCP text summaries

**Wave:** D | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):** `domain/findings.md`,
`domain/receipts.md`, `protocol/errors.md`
**Imported by:** `mcp/server.md` and compact rendering tests

## Purpose

This file renders the short human-readable text that accompanies each MCP tool result. The text is
for compatibility and quick inspection; it must never be stronger than the structured content.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `render_safe_compact_summary(envelope)` | return a bounded English-only summary for one tool result |
| `summary_for_status(...)` | status summary shape |
| `summary_for_check(...)` | check summary shape |
| `summary_for_receipt(...)` | receipt summary shape |
| `summary_for_public_error(...)` | error summary shape |

## Behavior

`render_safe_compact_summary(envelope)` inspects the structured result and emits a short text
summary with IDs, state, and limitations. It must not:

- print raw user payloads;
- claim more certainty than the structured result;
- introduce local-path or exception noise;
- translate `ok: false` into a success-looking sentence.

The per-operation helpers format the visible summary for each tool:

- `status` emphasizes frontier, freshness, open obligations, unresolved findings, and gaps;
- `check` emphasizes the verdict, the number of findings, and the exact bounded semantic
  status/reason pair;
- `receipt` emphasizes the recorded conclusion, frontier, and coverage limitations;
- public errors emphasize the code, retryability, and correlation ID.

The summaries are English-only in v0.1. They can be shorter than the structured content but must
remain faithful to it.

## Errors and edge cases

- The summary renderer must tolerate absent optional fields.
- If the structured object is malformed, the server should fail earlier; this file does not invent
  a second recovery path.
- A summary may omit detail, but it may not strengthen the conclusion.
- A `semantic_required` incomplete result may not omit its reason or substitute provider/error
  prose for the machine-readable code.

## Invariants

1. Summary text is always weaker than or equal to the structured content.
2. The renderer never outputs raw hidden payloads.
3. IDs and limitations stay visible.
4. English-only output is the v0.1 rule.

## Tests

- `tests/conformance/surfaces/test_cli_mcp_parity.py` — summary shape/wording and proof that text
  never exceeds structured content.

## Open questions

None.

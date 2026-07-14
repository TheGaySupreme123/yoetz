# tests/subprocess/test_mcp_stdout_purity.py — protocol-only stdout enforcement

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** MCP/observability specs and
child/frame helpers | **Imported by:** security, packaging, and release gates

## Purpose

Ensure no logger, warning, traceback, dependency banner, provider output, print, progress UI, or
shutdown message corrupts the MCP JSONL channel.

## Public surface

Cases inject startup warnings, invalid config, dependency logging, application finding/error,
unexpected exception, provider fake refusal/timeout, cancellation, signal, resource warning,
shutdown, and a deliberate test-only stdout-noise canary.

## Behavior

Capture stdout bytes from process start through exit before parsing. Every nonempty LF segment must
be a valid expected JSON-RPC frame, with no pre-initialize bytes or trailing partial data. Expected
operational diagnostics appear only as bounded stderr records; user-controlled canaries appear on
neither channel.

The deliberate injected stdout write must be intercepted/fail the test or create an invalid-frame
failure; it can never be filtered into a passing transcript. Run under warning-enabled Python and
dependency debug settings to expose accidental sinks, while production debug remains sanitized.

## Errors and edge cases

- Empty stdout is legal only for cases that terminate before a response by frozen transport policy.
- ANSI, whitespace banner, blank extra line, UTF-8 BOM, or logging prefix is contamination.
- Stderr loss does not authorize redirecting diagnostics to stdout.

## Invariants

1. MCP stdout is exclusively complete JSON-RPC frames.
2. Sanitization never repairs contaminated stdout after capture.
3. Diagnostic/user bytes cannot enter protocol framing accidentally.

## Tests

Run installed wheel in default/debug-warning configurations, with all six tools and every public
error class. Boundary scanner verifies exact injected canaries do not enter retained evidence.

## Open questions

None.

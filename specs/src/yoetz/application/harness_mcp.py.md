# src/yoetz/application/harness_mcp.py — MCP registration confirmation service

**Wave:** D | **ADRs:** ADR-010, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/ports/harness_mcp.py.md`, `specs/src/yoetz/ports/integrations.py.md`,
`specs/src/yoetz/domain/values.md` | **Imported by:** `specs/src/yoetz/cli/setup.py.md`

## Purpose

Binds every MCP registration mutation to an exact confirmed preview, mirroring
`IntegrationService`'s preview→confirm→execute discipline for the sibling `HarnessMcpPort`.
Without it, CLI callers would talk to adapters directly and confirmation semantics would drift
per call site.

## Public surface

- `McpRegistrationConfirmation` — `preview_digest`, `explicitly_accepted: bool`, `channel`
  (exactly `interactive|noninteractive_flag`).
- `McpRegistrationDiagnostic` — path-free structural observation: `harness` (`HarnessId`),
  `phase` (`preview|status|execute`), `outcome` (`success|failed`), optional
  `state_before`/`state_after`, optional `preview_digest`, and `reason` present exactly when
  `outcome` is `failed`.
- `HarnessMcpDiagnosticSink` — `Protocol` with `record_mcp_registration(diagnostic)`.
- `HarnessMcpService` — `status(binary)`, `preview(binary)`,
  `register(binary, confirmation)`, all async; constructor takes the port and an optional sink
  (a null sink when omitted).

## Behavior

`status` and `preview` validate the `HarnessBinary`, delegate to the port, and record one
success or failure diagnostic. `register` refuses before touching the port when
`explicitly_accepted` is false (`confirmation_required`), then constructs a
`McpRegistrationCommand` carrying the confirmation's exact `preview_digest`; digest staleness is
enforced by the adapter against a freshly recomputed preview. Every port
`McpRegistrationError` is recorded with its reason and re-raised unchanged. Non-dataclass inputs
raise `ValueError` with the shared `integration_request_invalid`/
`integration_confirmation_invalid`/`integration_diagnostic_invalid` tokens.

## Errors and edge cases

- A generic acceptance without the exact current preview digest can never mutate: the adapter
  re-previews and raises `preview_stale`.
- Diagnostic construction enforces `outcome`/`reason` coherence, so a failure can never be
  recorded without its reason nor a success with one.
- The service adds no retry, no force path, and no reason widening.

## Invariants

1. No mutation without `explicitly_accepted=True` plus the exact preview digest.
2. Diagnostics carry states, digests, and reason tokens only — never paths or harness output.
3. The service is harness-neutral: adding a harness adds no branch here.

## Tests

- `tests/unit/application/test_harness_mcp_service.py` — acceptance gate, digest passthrough,
  diagnostic recording, closed confirmation channel, invalid-input rejection.

## Open questions

None.

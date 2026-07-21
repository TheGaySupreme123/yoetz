# src/yoetz/ports/harness_mcp.py — harness MCP registration port

**Wave:** D | **ADRs:** ADR-010, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/ports/integrations.py.md`, `specs/src/yoetz/protocol/canonical.md`,
`specs/src/yoetz/domain/values.md` | **Imported by:**
`specs/src/yoetz/application/harness_mcp.py.md`,
`specs/src/yoetz/adapters/integrations/codex_mcp.py.md`, `specs/src/yoetz/cli/setup.py.md`

## Purpose

Owns the harness-neutral MCP server registration boundary as a sibling of `IntegrationsPort`.
Registration is a global, file-free mutation of a harness's own configuration; reusing the
skill-install types would misuse trusted-project fields (`project_root`, `file_changes`, managed
markers). Without this port, the ADR-012 wizard would either overload `IntegrationsPort` or talk
to subprocesses without preview/confirmation discipline.

## Public surface

- `MCP_SERVER_NAME` — the fixed registered server name, exactly `yoetz`.
- `MCP_SERVE_COMMAND` — the fixed serve argv, exactly `("yoetz", "mcp", "serve")`.
- `HarnessBinary` — one discovered harness executable: `harness_id` (`HarnessId`),
  `executable_path` (1..4096 printable chars, no control chars), `reported_version`
  (`str | None`, bounded printable ASCII), `compatibility` (`supported|untested`). `repr`/`str`
  redact the path.
- `McpRegistrationState` — closed enum `absent|yoetz_owned|foreign_present`.
- `McpRegistrationAction` — closed enum `register|noop`.
- `McpRegistrationReason` — closed enum `confirmation_required|preview_stale|
  harness_unavailable|parse_failed|timeout|registration_failed|foreign_entry_present`.
- `McpRegistrationPreview` — `harness_id`, `action`, `state_before`, sorted unique ASCII
  `warnings` (≤16), `preview_digest` (`sha256:` digest).
- `McpRegistrationCommand` — `preview_digest` plus `explicitly_accepted: bool`.
- `McpRegistrationResult` — `harness_id`, `action`, `state_before`, `state_after`,
  `preview_digest`.
- `McpRegistrationError(Exception)` — `reason` plus bounded `safe_details` (≤16 token keys,
  ≤4096 canonical bytes); never carries stdout/stderr/paths.
- `HarnessMcpPort` — `Protocol` with `status_registration(binary)`,
  `preview_registration(binary)`, and `apply_registration(binary, command)`, all async.

## Behavior

Every dataclass is frozen/slotted and validates in `__post_init__` using the shared
`ProtocolValueError` reason vocabulary (`integration_harness_invalid`,
`integration_action_invalid`, `integration_state_invalid`, `integration_target_invalid`,
`integration_value_invalid`, `integration_compatibility_invalid`, `integration_reason_invalid`,
`integration_error_invalid`, `invalid_digest`), with an out-of-registry reason falling back to
`invalid_event_value_type`. Digests validate through `validate_sha256_digest`. Warning tuples
must be strictly ascending unique ASCII tokens. The port itself is inert typed structure: no I/O,
no subprocess, no default adapter.

## Errors and edge cases

- Construction failures raise `ProtocolValueError` with the exact reason token; no partial value
  escapes.
- `McpRegistrationError.safe_details` rejects oversize maps and non-token keys so an adapter
  cannot smuggle raw harness output or filesystem paths into a bounded error.
- `HarnessBinary` rejects control characters and overlong paths before any adapter sees them.

## Invariants

1. The registered server identity is fixed: name `yoetz`, command `yoetz mcp serve` — never
   composed at runtime.
2. Registration types carry no project root, file inventory, or marker semantics; skill install
   and MCP registration remain separate facts (ADR-010, Codex runbook).
3. `compatibility` never claims `supported` without capability evidence; discovery-produced
   values are always `untested` under E-002.
4. Executable paths never appear in `repr`, exceptions, or diagnostics.

## Tests

- `tests/unit/adapters/test_codex_mcp_registration.py` exercises every state/action/reason value
  through the Codex adapter.
- `tests/unit/application/test_harness_mcp_service.py` locks confirmation/digest semantics over
  these types.

## Open questions

None.

# src/yoetz/cli/exits.py — public error code to process exit mapping

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):** `protocol/errors.md`
**Imported by:** `cli/app.md`, shell tests, and release tooling

## Purpose

This file maps public errors to process exit codes. It keeps shell automation stable without
collapsing the richer structured error information into the exit status.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `exit_code_for(...)` | map success/public error/cancellation to the approved exit code |
| `PUBLIC_EXIT_CODES` | stable exit-code mapping table |

## Behavior

`exit_code_for(...)` maps the public contract to the exit family:

- `0` — operation completed;
- `2` — usage or local input parsing error;
- `10` — protocol/session/frontier/idempotency conflict;
- `11` — operation is durably pending and should be retried with the same request ID;
- `20` — service unavailable/locked, privacy authority required, safe storage/runtime
  incompatibility, or required migration;
- `30` — a direct provider setup/probe/support operation, which has no completed deterministic
  check result, ended in a public provider error;
- `40` — corruption/security condition; writes disabled;
- `70` — sanitized internal software error;
- `130` — interrupted by user.

The function does not inspect Python exception subclasses directly. It consumes the public error code
or the success/cancellation outcome and returns the appropriate process exit. Findings alone do not
change the exit code; a nudge is not a failure.

## Errors and edge cases

- A conflicting or stale request does not become a generic nonzero exit without the specific
  conflict class.
- A completed operation with findings may still exit `0`.
- A completed check whose requested semantic phase is unavailable/refused/timed out/invalid exits
  `0` with `verdict=incomplete_check`; it never maps that semantic gap to `30`.
- `SERVICE_UNAVAILABLE`, `VAULT_LOCKED`, and `PRIVACY_AUTHORITY_REQUIRED` remain distinguishable in
  the structured result even though shell status groups them into exit family `20`.
- The caller is responsible for serializing the full structured result; this file only maps the
  process status.

## Invariants

1. Exit codes stay stable across CLI and MCP wrappers.
2. No findings-imply-failure shortcut.
3. Conflict, pending, storage, provider, corruption, internal, and interrupt states remain distinct.

## Tests

- `tests/subprocess/test_cli_streams_and_exits.py` — full mapping table and representative installed
  command outcomes.

## Open questions

None.

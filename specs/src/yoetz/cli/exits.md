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

`PUBLIC_EXIT_CODES` is total over every registered `PublicErrorCode` and contains exactly this
frozen mapping:

| Public outcome/code | Exit |
|---|---:|
| success | `0` |
| `INVALID_REQUEST` | `2` |
| `PROTOCOL_VERSION_UNSUPPORTED` | `20` |
| `SESSION_NOT_FOUND` | `10` |
| `SESSION_CONFLICT` | `10` |
| `IDEMPOTENCY_CONFLICT` | `10` |
| `OPERATION_PENDING` | `11` |
| `FRONTIER_CONFLICT` | `10` |
| `EVENT_INVALID` | `2` |
| `LIMIT_EXCEEDED` | `2` |
| `BUNDLE_BUSY` | `20` |
| `STORAGE_UNSAFE` | `20` |
| `STORAGE_CORRUPT` | `40` |
| `MIGRATION_REQUIRED` | `20` |
| `SERVICE_UNAVAILABLE` | `20` |
| `VAULT_LOCKED` | `20` |
| `PRIVACY_AUTHORITY_REQUIRED` | `20` |
| `PROVIDER_UNAVAILABLE` | `30` |
| `PROVIDER_REFUSED` | `30` |
| `PROVIDER_TIMEOUT` | `30` |
| `SEMANTIC_RESULT_INVALID` | `30` |
| `CANCELLED` | `130` |
| `INTERNAL_ERROR` | `70` |

Usage-parser failures that occur before a public envelope also exit `2`; a caught foreground
interrupt maps to the same `130` family as public `CANCELLED`. No unknown public enum member is
accepted: import-time/test-time exhaustiveness requires set equality between
`PUBLIC_EXIT_CODES` and `PublicErrorCode`; a missing or extra member is a build failure.

The function does not inspect Python exception subclasses directly. It consumes the public error code
or the success/cancellation outcome and returns the appropriate process exit. Findings alone do not
change the exit code; a nudge is not a failure.

## Errors and edge cases

- A conflicting or stale request does not become a generic nonzero exit without the specific
  conflict code; read-only status future-frontier input is `INVALID_REQUEST`/`2`, not
  `FRONTIER_CONFLICT`/`10`.
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

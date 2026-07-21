# tests/unit/adapters/test_codex_mcp_registration.py — Codex MCP adapter unit gate

**Wave:** D | **ADRs:** ADR-010, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/integrations/codex_mcp.py.md` | **Imported by:**
`specs/tests/unit.md`

## Purpose

Locks the check-then-add automation against a scripted runner: state classification, digest
binding, verify-by-reread, and the never-replace-foreign rule.

## Public surface

Pytest module; no exports.

## Behavior

Covers: nonzero `get` classifies `absent` with the exact argv recorded; accepted entry shapes
(top-level `command`/`args`, `command` list, and Codex ≥0.144.6 nested `transport`) classify
`yoetz_owned`; different/unreadable commands (including foreign nested `transport`) classify
`foreign_present`; non-JSON, non-object, and non-UTF-8 stdout raise `parse_failed`; previews
select `register`/`noop` with the foreign warning; apply refuses without acceptance
(`confirmation_required`) and with a wrong digest (`preview_stale`); a successful apply runs
exactly `mcp add yoetz -- yoetz mcp serve` and only reports success after re-reading
`yoetz_owned`; a verify that still shows `absent` raises `registration_failed`; a foreign entry
is refused before any `add` runs; an owned state applies as a pure no-op with only `get` calls.

## Errors and edge cases

All subprocess behavior is scripted; no real `codex` is ever invoked.

## Invariants

1. Every mutating path in the adapter is exercised with its refusal twin.

## Tests

Self; indexed by `specs/tests/unit.md`.

## Open questions

None.

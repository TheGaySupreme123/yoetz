# tests/unit/adapters/test_codex_plugin.py — plugin render/install unit tests

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):**
`src/yoetz/adapters/integrations/codex_plugin.py.md` | **Imported by:** adapter unit suite

## Purpose

Prove the rendered plugin tree wires the three hooks, install refuses an empty tested set, refuses
overwrite of modified files when profiled, and inspection reports trust as not observable.

## Public surface

pytest cases for render, fail-closed install, modified refusal, absent inspection.

## Behavior

Uses the shared skill resource fixture. Installation with a non-empty tested set is injected only to
exercise the modified-file refusal path; default empty tested set remains the production posture.

## Errors and edge cases

`version_incompatible` and `modified_copy` are asserted.

## Invariants

1. No support claim from file presence.
2. Three hook commands present in hooks.json.

## Tests

This file.

## Open questions

None.

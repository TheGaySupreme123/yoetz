# tests/subprocess/test_hooks_cli.py — hooks CLI inactive path

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `src/yoetz/cli/hooks.py.md`,
`src/yoetz/cli/app.md` | **Imported by:** subprocess suite

## Purpose

Exercise `yoetz hooks session-start` through the Typer CLI for the no-mapping inactive case.

## Public surface

CliRunner invocation of `hooks session-start` with a resume payload and isolated state dir.

## Behavior

Stdout JSON additionalContext states that no Yoetz task is mapped and does not invent a task id.

## Errors and edge cases

Exit code remains 0.

## Invariants

Honest inactive disclosure only.

## Tests

This file.

## Open questions

None.

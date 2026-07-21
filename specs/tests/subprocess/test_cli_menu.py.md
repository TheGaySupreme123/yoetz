# tests/subprocess/test_cli_menu.py — interactive menu behavior gate

**Wave:** D/F | **ADRs:** ADR-012, ADR-013 | **Imports (spec-tree):**
`specs/src/yoetz/cli/menu.md`, `specs/src/yoetz/cli/app.md` | **Imported by:**
`specs/tests/subprocess.md`

## Purpose

Locks the ADR-013 menu gates through `CliRunner` with faked TTY probes, faked discovery, and an
unreachable service client — no real Codex, service, or state directory is touched.

## Public surface

Pytest module; one `menu_env` fixture.

## Behavior

Covers: `yoetz menu` on a non-TTY is a usage failure (exit 2) that never prompts; with TTY
probes faked true the home screen renders the status overview (unreachable service line naming
`yoetz service run`, per-binary harness line, first-run posture) and the six sections, and `q`
quits with exit 0; section navigation returns to the home screen via `b`; a bare invocation with
the marker present and TTY probes faked true dispatches to the menu; a bare non-TTY invocation
still prints help with exit 0 (regression companion to the ADR-012 tests).

## Errors and edge cases

Fakes are injected via monkeypatch on the owning module names (`yoetz.cli.menu`,
`yoetz.cli.app`, `yoetz.cli.setup`, discovery); no test depends on a real terminal.

## Invariants

1. No test writes outside `tmp_path` or reaches a real PATH binary or service endpoint.

## Tests

Self; indexed by `specs/tests/subprocess.md`.

## Open questions

None.

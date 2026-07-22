# tests/subprocess/test_setup_wizard_cli.py — setup wizard CLI behavior gate

**Wave:** D/F | **ADRs:** ADR-007, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/cli/setup.py.md`, `specs/src/yoetz/cli/app.md` | **Imported by:**
`specs/tests/subprocess.md`

## Purpose

Locks the end-to-end wizard and `integrate mcp` CLI behavior through `CliRunner` with faked
discovery, scripted adapter subprocesses, a faked marker path, and an unreachable service
client — no real Codex, service, or state directory is touched.

## Public surface

Pytest module; one `wizard_env` fixture.

## Behavior

Covers: a non-interactive run without `--accept` is a dry run (declined registration, no marker,
next steps naming `yoetz service run`, `yoetz privacy setup`, and `yoetz provider credential
set`); `--accept` registers through the exact get/get/add/get sequence and writes the
`yoetz.setup-wizard-marker/1` marker; a foreign entry is preserved with only `get` invocations;
an interactive run lists the detected `Codex` harness for connection to `Yoetz`, separately lists
multiple Codex installations, prints a branded preview, rejects answers other than explicit `Y` or
`N`, and registers only after `Y`;
multiple candidates fail closed non-interactively with a `--codex-path` message and exit 2; zero
candidates still complete with `codex_not_found` guidance; `setup status` is read-only with the
`yoetz.setup-status/1` schema; the `integrate codex mcp` status/preview/install matrix including
digest binding; install without acceptance and against a foreign entry fails closed with exit 2;
no secret-shaped option appears in any `setup` help text; a bare non-TTY invocation prints help
with exit 0; `--fireworks` / `--model` / `--api-key` without `--set` exit 2 with a usage failure
whose plain (ANSI-stripped) output contains `require --set`; a successful `--set` path reports
layer-separated provider readiness (binding/credential supported; SDK extra / semantic evaluator /
privacy policy / transport probe / installed artifact separately as present or not demonstrated)
and never prints “Yoetz is ready to use this provider.”

## Errors and edge cases

Every fake is injected via monkeypatch on the `yoetz.cli.setup` module names; the marker path is
a `tmp_path` file so `config/paths` safety gates are bypassed deliberately and separately
tested.

## Invariants

1. No test writes outside `tmp_path` or reaches a real PATH binary.

## Tests

Self; indexed by `specs/tests/subprocess.md`.

## Open questions

None.

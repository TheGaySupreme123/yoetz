# tests/unit/adapters/test_codex_discovery.py — Codex discovery unit gate

**Wave:** D | **ADRs:** ADR-005, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/integrations/codex_discovery.py.md` | **Imported by:**
`specs/tests/unit.md`

## Purpose

Locks the discovery contract without a real PATH or Codex binary, using the `CodexProbe` seam.

## Public surface

Pytest module; no exports.

## Behavior

Covers: empty/missing PATH entries yield an empty tuple; a single candidate reports the exact
PATH-visible path, parsed `x.y.z` version, and always-`untested` compatibility; failed or
unparsable version probes yield `reported_version=None`; a symlinked duplicate is deduplicated
by resolved target while keeping the PATH-visible name; two distinct installs are both reported
in sorted order; a non-executable candidate is skipped; discovery leaves candidate bytes and
mode untouched.

## Errors and edge cases

Fake probes only; the tests create real temp files solely as inert discovery targets.

## Invariants

1. No test invokes a real subprocess or mutates anything outside `tmp_path`.

## Tests

Self; indexed by `specs/tests/unit.md`.

## Open questions

None.

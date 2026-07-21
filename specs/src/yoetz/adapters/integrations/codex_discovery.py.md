# src/yoetz/adapters/integrations/codex_discovery.py — Codex PATH discovery

**Wave:** D | **ADRs:** ADR-005, ADR-010, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/ports/harness_mcp.py.md`, `specs/src/yoetz/ports/integrations.py.md` |
**Imported by:** `specs/src/yoetz/cli/setup.py.md`

## Purpose

Pure, read-only enumeration of installed Codex CLI executables so the ADR-012 wizard can present
real candidates instead of guessing. Discovery is observation only: it claims no capability
support and mutates nothing.

## Public surface

- `CodexProbe` — `Protocol` injection seam with `path_entries() -> tuple[str, ...]` and
  `run_version(executable: str) -> str | None`, so tests never touch a real PATH or binary
  (mirrors the `_PathProbe` seam in `config/paths`).
- `discover_codex_binaries(*, _probe=None) -> tuple[HarnessBinary, ...]`.

## Behavior

The default probe splits `$PATH` on `os.pathsep` (empty entries dropped) and runs
`<candidate> --version` with `shell=False`, stdin devnull, captured output, and a 5-second
timeout; a nonzero exit, OS/subprocess error, or non-UTF-8 output yields `None`. Discovery walks
PATH entries in order, forms the absolute candidate `<entry>/codex`, resolves it strictly for a
dedupe key (so aliased installs count once) while keeping the PATH-visible name as
`executable_path` (that is what registration must invoke), keeps only regular executable files,
parses the first output line for the first `\d+.\d+.\d+` token as `reported_version`, caps
results at 16 candidates, and returns them sorted by `executable_path`. Every candidate reports
`compatibility="untested"`.

## Errors and edge cases

- A vanished, unreadable, or non-executable candidate is silently skipped; discovery never
  raises for an individual entry.
- Version probe failures produce `reported_version=None`, never an exception or a fabricated
  version.
- Output beyond 4096 bytes is truncated before parsing.

## Invariants

1. Discovery never executes anything except `<candidate> --version`, never writes, and never
   claims `supported` (E-002: a version string is not capability evidence).
2. Results are deterministic for a fixed probe: dedupe by resolved target, stable sort order,
   bounded count.
3. The PATH-visible executable name is preserved; symlink targets are dedupe keys only.

## Tests

- `tests/unit/adapters/test_codex_discovery.py` — empty/single/multiple/dedupe/non-executable
  cases, version parsing, and a no-mutation check, all through a fake probe.

## Open questions

None.

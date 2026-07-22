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

The default probe splits `$PATH` on `os.pathsep` (empty entries dropped), then augments it with
reviewed app-bundle resource directories. On macOS it appends
`/Applications/ChatGPT.app/Contents/Resources`. On Windows it runs one bounded, read-only,
non-interactive PowerShell package query for the Microsoft Store package family
`OpenAI.Codex_2p2nqsd0c76g0` and appends each returned `InstallLocation/resources` directory.
PowerShell absence, timeout, nonzero exit, invalid UTF-8, overlong/control-character output, a
missing package, or a missing resource directory yields no app candidate rather than an invented
path. The official app is not currently distributed for Linux, so Linux adds no synthetic app
directory and still discovers every reviewed CLI candidate on PATH.

On POSIX, each directory considers exactly the executable names `codex` and `codex-testing`. On
Windows each logical installation considers `.exe`, then `.cmd`, then the extensionless form and
keeps only the first existing executable form, preventing npm shims for one install from appearing
twice. Discovery never performs wildcard or prefix matching: names such as `codex-testing-update`
are not candidates. For each exact candidate it runs
`<candidate> --version` with `shell=False`, stdin devnull, captured output, and a 5-second
timeout; a nonzero exit, OS/subprocess error, or non-UTF-8 output yields `None`. Discovery walks
PATH entries in order, forms each absolute candidate, resolves it strictly for a dedupe key (so
aliased installs count once) while keeping the PATH-visible name as
`executable_path` (that is what registration must invoke), keeps only regular executable files,
parses the first output line for the first SemVer 2.0 token — `X.Y.Z` with optional `-prerelease`
and `+build` suffixes using ASCII alphanumerics, hyphen, and dot — as `reported_version` without
truncating a prerelease to its release core, caps results at 16 candidates, and returns them sorted
by `executable_path`. Every candidate reports `compatibility="untested"`.

## Errors and edge cases

- A vanished, unreadable, or non-executable candidate is silently skipped; discovery never
  raises for an individual entry.
- Version probe failures produce `reported_version=None`, never an exception or a fabricated
  version.
- Output beyond 4096 bytes is truncated before parsing.

## Invariants

1. Discovery executes only the bounded read-only Windows package-location query when applicable
   and exact allowlisted `<candidate> --version` probes; it never writes and never claims
   `supported` (E-002: a version string is not capability evidence).
2. Results are deterministic for a fixed probe: dedupe by resolved target, stable sort order,
   bounded count.
3. The PATH-visible executable name is preserved; symlink targets are dedupe keys only.

## Tests

- `tests/unit/adapters/test_codex_discovery.py` — empty/single/multiple/dedupe/non-executable
  cases, exact `codex-testing` inclusion with prefix-neighbor exclusion, standard macOS Desktop
  augmentation, Windows Store app plus `.exe`/`.cmd` handling, Linux CLI behavior, version parsing
  including exact prerelease preservation for `0.146.0-alpha.2`, and a no-mutation check.

## Open questions

None.

# support/npm-launcher/bin/yoetz.js — delegation-only launcher script

**Wave:** F | **ADRs:** ADR-007, ADR-012 | **Imports (spec-tree):**
`specs/support/npm-launcher/package.json.md` | **Imported by:** `specs/tests/packaging.md`

## Purpose

The executable behind `npx yoetz`. It delegates one invocation to the exact pinned Python
distribution through `uv` and does nothing else, so the Python CLI remains the single owner of
every behavior — including the ADR-012 first-run wizard on a bare interactive invocation.

## Public surface

A Node CLI script (`#!/usr/bin/env node`, CommonJS, no imports beyond `node:child_process` and
its own `package.json`). No exported API.

## Behavior

1. Probe `uv --version` (`spawnSync`, `shell: false`, stdio ignored). On failure, print a
   bounded stderr message naming `uv`, pointing at the official
   `docs.astral.sh/uv/getting-started/installation/` instructions, stating the launcher only
   delegates to `yoetz==<version>` and never installs anything itself; exit 1. It never
   auto-installs `uv`.
2. Otherwise run `uvx yoetz==<version> <argv...>` with `stdio: "inherit"` and `shell: false`,
   where `<version>` is read from its own `package.json` and `<argv...>` is
   `process.argv.slice(2)` untouched — no flag rewriting, no argument injection, no wizard
   logic duplicated here.
3. Exit with the child's exact exit code; a spawn error prints one bounded stderr line and
   exits 1; a null status maps to 1.

## Errors and edge cases

- Missing `uv` and spawn failures are the only launcher-owned failures; every other outcome —
  including exit codes, prompts, and TTY behavior — belongs to the delegated Python process via
  inherited stdio.
- The launcher writes nothing to disk and reads nothing but its own `package.json`.

## Invariants

1. Exact version pinning: the delegated requirement is always `yoetz==<package.json version>`,
   never an unpinned name.
2. Argument and exit-code transparency.
3. No network, filesystem, or environment mutation of its own; no `shell: true` anywhere.

## Tests

- `tests/packaging/test_npm_launcher.py` — shebang/executability, exact pinned passthrough and
  exit-code propagation against a recorded fake `uvx` shim, and the missing-`uv` guidance path
  (both skipped cleanly when `node` is absent, since Node is contributor-only tooling).

## Open questions

None.

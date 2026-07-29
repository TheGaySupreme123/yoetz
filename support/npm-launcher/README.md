# yoetz npm launcher

This package is a **delegation launcher only**. `npx yoetz` (once published) runs the exact
pinned Python distribution `yoetz==<this package's version>` through
[`uv`](https://docs.astral.sh/uv/), which must already be installed. The launcher:

- bundles no Python, no dependencies, and no Yoetz code;
- downloads nothing itself — `uvx` performs the provenance-carrying install from PyPI;
- passes every argument through unchanged and exits with the child's exit code, reporting the
  conventional `128+n` when the child is killed by a signal so `npx yoetz` and the Python
  console script are interchangeable in a script that inspects exit codes;
- inherits stdio, so the child process sees the *real* terminal. This is load-bearing twice
  over: the same stdin/stdout TTY checks gate the full-screen interface (ADR-017), and the
  confidential credential ceremony requires the controlling terminal. Capturing or replacing
  these streams here would silently downgrade both.
- keeps its version in lockstep with the PyPI `yoetz` version, so the two distribution
  surfaces can never silently drift.

A bare interactive `npx yoetz` therefore reaches the same full-screen first run the Python CLI
owns; this package adds no behavior of its own and duplicates no setup or interface logic.

## When `uv` is missing

The packaging contract forbids bootstrapping a runtime from here, so the launcher does not try.
It prints what is missing, the platform-appropriate install command, and the fact that it
installs nothing itself — then exits 1 without running anything.

## Publication status

**This package is deliberately unpublished.** `"private": true` in `package.json` makes
`npm publish` refuse it. Publishing is a separate, deliberate release decision recorded in
ADR-012: flip `private` to `false`, verify the registry name, and follow the ordinary release
review — never publish as a side effect of another change.

# yoetz npm launcher

This package is a **delegation launcher only**. `npx yoetz` (once published) runs the exact
pinned Python distribution `yoetz==<this package's version>` through
[`uv`](https://docs.astral.sh/uv/), which must already be installed. The launcher:

- bundles no Python, no dependencies, and no Yoetz code;
- downloads nothing itself — `uvx` performs the provenance-carrying install from PyPI;
- passes every argument through unchanged and exits with the child's exit code;
- keeps its version in lockstep with the PyPI `yoetz` version, so the two distribution
  surfaces can never silently drift.

A bare interactive `npx yoetz` therefore reaches the same first-run setup wizard the Python
CLI owns; this package adds no behavior of its own.

## Publication status

**This package is deliberately unpublished.** `"private": true` in `package.json` makes
`npm publish` refuse it. Publishing is a separate, deliberate release decision recorded in
ADR-012: flip `private` to `false`, verify the registry name, and follow the ordinary release
review — never publish as a side effect of another change.

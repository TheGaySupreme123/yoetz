# yoetz npm launcher

This package is a **delegation launcher only**. `npx yoetz` runs the exact
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

## Publication and provenance

The v0.1.0 maintainer decision publishes this launcher as the public `yoetz` package. The tagged
release workflow builds its tarball once, records its SHA-256, publishes the exact tarball through
npm trusted publishing only after the matching `yoetz==0.1.0` Python distribution is live, and
downloads it back for byte comparison. npm's package provenance binds the public tarball to that
GitHub Actions workflow.

The launcher remains only a delegator. npm installation does not install Python, `uv`, Yoetz's
Python code, or any runtime dependency; it selects the same exact-version PyPI distribution that
the release workflow already published and verified.

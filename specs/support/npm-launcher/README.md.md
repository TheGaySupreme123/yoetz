# support/npm-launcher/README.md — launcher provenance and publication statement

**Wave:** F | **ADRs:** ADR-007, ADR-012 | **Imports (spec-tree):**
`specs/support/npm-launcher/package.json.md` | **Imported by:** future npm package consumers

## Purpose

The user-facing README packaged with the launcher. It must state, before publication ever
happens, exactly what the package does and does not do, so the npm listing can never overclaim.

## Public surface

A short Markdown document with two sections: the delegation contract and the publication
status.

## Behavior

The delegation section states: the package runs `yoetz==<version>` via `uv`/`uvx`, which must
already be installed; it bundles no Python, no dependencies, and no Yoetz code; it downloads
nothing itself; it passes arguments through unchanged and propagates the child exit code; its
version is locked to the PyPI version; a bare interactive `npx yoetz` reaches the Python-owned
first-run wizard. The publication section states the package is deliberately unpublished, that
`"private": true` makes `npm publish` refuse it, and that publishing is a separate deliberate
release decision recorded in ADR-012 — never a side effect of another change.

## Errors and edge cases

- The README never claims installation, verification, signing, or capability support beyond
  delegation; honesty-rule wording applies to it like any public document.

## Invariants

1. Description and reality match the `package.json` and script specs exactly; drift is a review
   failure.

## Tests

- Reviewed with `tests/packaging/test_npm_launcher.py`'s shape checks; prose is review-bound
  rather than machine-verified.

## Open questions

None.

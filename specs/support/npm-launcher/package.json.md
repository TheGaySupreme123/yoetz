# support/npm-launcher/package.json — npm launcher manifest

**Wave:** F | **ADRs:** ADR-007, ADR-012 | **Imports (spec-tree):**
`specs/support/npm-launcher/bin/yoetz.js.md` | **Imported by:**
`specs/tests/packaging.md`, `specs/support/npm-launcher/README.md.md`

## Purpose

The publish-ready manifest of the `npx yoetz` distribution surface. It exists so the launcher
has an exact reviewed shape — and so `"private": true` can serve as the load-bearing guarantee
that the surface stays unpublished until a separate deliberate release decision.

## Public surface

A single JSON object with exactly these semantics:

- `name`: `yoetz` — the intended registry name (availability is verified at publication time,
  not now).
- `version`: byte-identical to the Python `yoetz.__version__`; the two surfaces move in
  lockstep.
- `private`: `true` — `npm publish` refuses the package; flipping this is the one deliberate
  future publication action (ADR-012 decision 6).
- `bin`: `{"yoetz": "./bin/yoetz.js"}`.
- `files`: the launcher script and README only.
- `license`: `Apache-2.0`; `description` states the delegation-only role.
- `engines`: an advisory Node floor; end users only need whatever runs `npx`.
- **No** `dependencies`, `devDependencies`, `optionalDependencies`, or `scripts` keys — the
  launcher may never bundle or fetch code, and no install hook may run.

## Behavior

Inert data consumed by npm. The version field is read at runtime by `bin/yoetz.js` to pin the
exact `uvx yoetz==<version>` invocation.

## Errors and edge cases

- A version drift against the Python package is a test failure, not a runtime behavior.
- Adding any dependency key is a contract break caught by the packaging test.

## Invariants

1. `private` stays `true` in this repository; publication is never a side effect.
2. No dependency, script, or hook of any kind.
3. Version lockstep with the Python distribution.

## Tests

- `tests/packaging/test_npm_launcher.py` — shape assertions (private, bin, forbidden keys,
  license) and the version-lockstep check.

## Open questions

None. The registry-name availability check is deferred to the future publication decision.

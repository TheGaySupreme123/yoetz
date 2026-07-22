# tests/packaging/test_version_manifest.py — installed version/provenance truthfulness

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):** version,
resource manifest, migration/policy/projection/object/provider specs | **Imported by:** support and
release evidence

## Purpose

Verify `yoetz version --json` and package metadata report exact installed identities without
checkout dependence, unstated inference, private paths, or stronger support claims.

## Public surface

Cases cover source candidate, wheel clean install, sdist-rebuilt wheel, offline reinstall, absent
optional extras, supported/unsupported platform, modified resource, and missing provenance field.

## Behavior

Invoke installed console and module entry from unrelated cwd with network denied. Parse strict JSON
and compare package/tag/commit/release provenance; protocol/engine/projection/policy/object/encryption/
migration/resource versions/digests; Python/platform; APSW/SQLite source/options; MCP SDK and optional
provider adapter availability to independently probed candidate manifests.

The default resource summary must report the exact 72-entry category counts and digest without
enumerating entries; explicit `--resources` must enumerate exactly those same 72 reviewed identities
and no ambient package files.

Differentiate built/tested/supported/available/not-installed/unknown. Do not infer Git state from
cwd, provider capability from installed SDK, or platform support from version string alone. Human
render is a bounded projection of JSON. Output is deterministic except explicitly observed runtime
identity and contains no local path, username, environment, credential, config, bundle, or payload.

## Errors and edge cases

- Missing/invalid resource manifest yields bounded integrity state and write refusal, not fabricated
  version values.
- Unknown commit/signature is explicit; checksums are not signatures.
- Unsupported runtime may run structural version inspection but must not claim writable support.
- Console/module outputs must agree.

## Invariants

1. Every reported identity is measured or embedded provenance.
2. Availability and support/capability are distinct.
3. Output is path/private/secret-free.
4. Metadata, artifact name, tag, and runtime report agree.

## Tests

Golden structural manifests plus mutation cases for each identity/claim status; boundary scanner
seeds canary checkout/home paths and optional provider secrets.

## Open questions

None.

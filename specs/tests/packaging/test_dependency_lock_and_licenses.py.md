# tests/packaging/test_dependency_lock_and_licenses.py — locked dependency and legal inventory gate

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** pyproject/uv/npm lock, SBOM/release policy
specs | **Imported by:** security and release gates

## Purpose

Prove declared/locked/built/installed dependency sets agree for every target and every shipped
component has reviewed source/hash/license/disposition without private/local dependency routes.

## Public surface

Cases validate Python base/extras/platform markers, Node dev-only tool lock, hashes/sources, wheel
tags, direct/transitive inventory, installed metadata, native component identity, licenses/notices,
and vulnerability-disposition inputs.

## Behavior

Run lock check without update. Resolve each advertised target/extras from lock and compare expected
distribution/version/hash/source to wheelhouse, artifact metadata and clean installed inventory.
Reject editable/path/Git/private-index/unhashed/ambiguous dependency and unexpected transitive
package. Node tooling uses `npm ci --ignore-scripts` and cannot enter runtime artifact.

Build canonical component/license inventory including APSW-bundled SQLite, cryptography/native
libraries and build tools where provenance policy requires. Match normalized license identifiers and
required notice text to reviewed allowlist; unknown/incompatible/missing license blocks. Vulnerability
disposition references stable advisory/package/version, owner/expiry and cannot waive license/hash.

## Errors and edge cases

- Registry/advisory outage cannot update lock; offline known inputs remain evidence only within
  freshness policy.
- Metadata license text alone does not override packaged source/license mismatch.
- Platform marker resolution must be tested per target, not host-only.
- Reports contain public package data, never index credentials/private URLs.

## Invariants

1. Declared, locked, built, wheelhouse, installed and SBOM sets reconcile.
2. Every dependency is hash/source/license accountable.
3. Dev tooling never ships as runtime dependency/content.
4. Unknown/private/unhashed inputs block release.

## Tests

Synthetic lock/metadata mutations cover missing hash, extra transitive, marker drift, local URL,
license conflict, expired disposition and native identity mismatch.

## Open questions

None.

# tests/packaging/test_offline_reinstall.py — hash-locked network-denied reinstall

**Wave:** F | **ADRs:** ADR-003, ADR-007 | **Imports (spec-tree):** lock/artifact/install/resource
specs | **Imported by:** release recovery/supply-chain gate

## Purpose

Prove the captured release artifact set is sufficient to install/reinstall strict-local Yoetz with
network fully denied and without compiling/downloading an unreviewed SQLite/native variant.

## Public surface

Cases: clean offline install, uninstall/reinstall with retained data, base and each advertised extra,
missing dependency wheel, wrong-platform wheel, corrupted hash, empty cache, and attempted source
build/network fallback.

## Behavior

Build a wheelhouse from the explicit release manifest: candidate, all locked target distributions,
hashes, installer/runtime artifacts permitted by policy. Start a clean environment with empty
package caches, DNS/socket denial and only wheelhouse access. Install with exact hashes/no-index/no-
build-isolation fallback, run version/resource/startup and strict-local six-operation smoke.

Uninstall package while retaining app data, then reinstall from same immutable wheelhouse and replay
receipt. Repeat extras. Remove or corrupt one required wheel and assert installer fails before
partial usable environment; it never contacts registry, compiles APSW/cryptography, relaxes hash, or
selects incompatible file.

## Errors and edge cases

- Network monitor failure or attempted connection fails even if install succeeds from cache.
- Wheelhouse contains only target allowlist; local source checkout/build caches are absent.
- Platform mismatch is explicit; sdist presence cannot authorize local native build.
- Evidence lists public filenames/digests, no internal mirror credential/path.

## Invariants

1. Captured hashed artifacts alone reproduce installation.
2. No network or unreviewed build fallback occurs.
3. Reinstall preserves/reads retained compatible user data.
4. Missing/corrupt input fails before support claim.

## Tests

Run in clean network-isolated containers/VMs on each advertised target and scan process/network/build
logs structurally before retaining evidence.

## Open questions

None.

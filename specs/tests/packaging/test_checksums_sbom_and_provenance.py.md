# tests/packaging/test_checksums_sbom_and_provenance.py — release evidence integrity and wording

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** release-evidence generator, dependency/
license/capability/support specs | **Imported by:** tagged release and post-publication verification

## Purpose

Verify checksums, CycloneDX SBOM, build provenance/attestation status, support matrix and release
evidence all describe the same tested candidate bytes and use honest verification language.

## Public surface

Cases validate `SHA256SUMS`, SBOM components/relationships/hashes/licenses, provenance subject/
builder/source/materials, release-evidence self/input digests, capability/support/limitation links,
signature status, offline verify commands, and downloaded artifact equality.

## Behavior

Independently hash each candidate artifact and published evidence file; parse checksum file and
require intended files exactly once, stable filename ordering and lowercase SHA-256. Reconcile SBOM
components with lock/wheelhouse/installed inventory including native SQLite component; no duplicate,
missing or unknown package.

Validate provenance subject digests equal artifacts, source tag/commit and locked build materials/
tool/runner identity. Distinguish checksum, CI provenance, cryptographic attestation and signature;
when no user-verifiable signature exists, output explicitly says `not_provided`. Capability matrix,
support and limitations must trace to passing exact evidence cells.

Run documented `VERIFY.md` commands offline on copied/downloaded files, then mutate every digest,
component, subject, candidate ID, support cell and signature claim to prove failure.

## Errors and edge cases

- A self-referential checksum set follows the defined enclosing-manifest rule; it cannot omit an
  artifact silently.
- Unknown SBOM/provenance schema field/version or parser failure blocks.
- Build-service outage is incomplete/explicit, never forged local signature.
- Evidence contains no private paths/secrets/test transcripts and passes boundary scan.

## Invariants

1. Checksums/SBOM/provenance/evidence bind identical candidate bytes.
2. Every shipped dependency appears once in SBOM reconciliation.
3. Support claims trace to passing evidence.
4. Checksums are never called signatures.
5. End-user verification commands are executable and tested.

## Tests

Use real release dry-run outputs plus exhaustive structural mutations and a post-download byte check
on every advertised platform.

## Open questions

None.

E-008 is the sole central release-evidence gate; signing remains deferred to v0.2.

# tests/packaging/test_private_boundary_and_secret_scan.py — publication-boundary mutation suite

**Wave:** F | **ADRs:** ADR-004, ADR-007 | **Imports (spec-tree):** public-boundary scanner/privacy/
artifact inventory specs | **Imported by:** security and release gates

## Purpose

Prove source and every built/public evidence artifact reject private/local/business material,
transcripts, credentials, customer/tenant/production identifiers, paths, unexpected files, and
known canaries without disclosing matches.

## Public surface

Targets: source export, sdist, wheel, metadata/RECORD, schemas/migrations/skills/fixtures/docs,
help/version output, SBOM/provenance/checksums/support/evidence. Mutation placements: filename, text,
binary, archive metadata, encoded form, split chunk and nested supported archive.

## Behavior

Run the scanner against the clean candidate and require full inventory/zero findings. For each rule
category, create a synthetic candidate with unique canary at each placement and require blocking
finding with stable rule/category/path/digest/location bucket—but no matched content/context/canary/
absolute path in console/report. Unknown file/type/parser limit/incomplete scan also blocks.

Test exact reviewed exceptions: right rule+path+digest passes; changed path/digest/expired owner fails.
Secrets/private keys cannot be excepted. Seed checkout/home/user/repository canaries and prove build/
help/version/evidence never embed them. Scan the scanner report itself.

## Errors and edge cases

- Binary UTF-8 failure is not clean; raw bytes still scan.
- Archive traversal/collision/compression bomb fails before extraction.
- Ignored status is not authorization if a file enters candidate.
- Test uses only synthetic canaries, never reads real private files/secrets.

## Invariants

1. No candidate member escapes scanning.
2. Any match/incomplete state blocks publication.
3. Reports never reveal matched bytes.
4. Exceptions are exact/reviewed and cannot permit secrets.

## Tests

One positive clean candidate plus exhaustive mutation table runs in PR/release; artifact mutations are
destroyed and never uploaded beyond redacted report.

## Open questions

None.

E-008 is the sole central artifact-boundary gate.

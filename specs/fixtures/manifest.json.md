# fixtures/manifest.json — immutable fixture corpus inventory

**Wave:** A–F | **ADRs:** ADR-002 through ADR-009 | **Imports (spec-tree):** every `fixtures/**/*.case.json` spec | **Imported by:** all test suites and release evidence generation

## Purpose

Freeze the complete public v0.1 fixture file set and make additions, removals, or byte changes explicit and reviewable.

## Public surface

A canonical strict-JSON object with exact top-level fields
`manifest_schema: "yoetz.fixture-manifest/1.0.0"`, `manifest_version: "1.0.0"`, and `members`.
Each member has `path`, `fixture_id`, `media_type`, `byte_length`, and lowercase `sha256`.

## Behavior

List exactly the 49 `*.case.json` resources owned by this directory tree, ASCII-sorted by relative POSIX path. The manifest never lists itself. `media_type` is `application/vnd.yoetz.fixture-case+json`. Generation verifies each case against the common fixture shape, rejects duplicate IDs or paths, computes bytes from the checked-in file without newline rewriting, and emits canonical JSON. The eight `fixtures/privacy/PRIV-*.case.json` members are public test/sdist evidence and are not installed package resources.

## Errors and edge cases

An unlisted case, missing listed case, digest or size mismatch, duplicate ID, noncanonical JSON, absolute path, symlink, or private-path/canary match fails validation and release packaging.

## Invariants

The manifest is deterministic, contains no timestamps or host data, and covers the entire finite fixture corpus. Released member bytes are compatibility inputs and are never silently rewritten.

## Tests

`tests/conformance/compatibility/test_resource_manifest.py`, `tests/packaging/test_resource_byte_parity.py`, and the fixture meta-validation exercised by every suite.

## Open questions

None.

# CHANGELOG.md — public release history and user-visible deltas

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** `src/yoetz_core/version.md`,
`tests/packaging.md`
**Imported by:** release notes, support triage, and upgrade planning

## Purpose

This file records what changed in each public release in a way that users, packagers, and support
staff can read without reconstructing the commit history.

## Public surface

The changelog must contain:

- an `Unreleased` section or equivalent in-progress bucket;
- versioned release entries;
- dates for released versions;
- user-visible change summaries;
- migration or support notes when a release requires them;
- a clear distinction between features, fixes, breaking changes, and security notices.

## Behavior

The changelog is a user-facing release ledger. Each entry should be concise enough to skim but
specific enough to tell a user whether the release affects them.

The file should:

- keep releases in reverse chronological order;
- use one stable format consistently;
- avoid internal implementation speculation;
- capture breaking changes explicitly;
- note security fixes without burying them in generic change bullets;
- mention migration or compatibility concerns when they matter to upgrades.

The changelog must not duplicate every commit. It is a release artifact, not a source control log.
It should reflect the public contract and the user-facing consequences of a release.

v0.1 uses a lightweight project-native heading format, not a verbatim external template, and keeps
one permanent `Unreleased` section above reverse-chronological released versions.

## Errors and edge cases

- A changelog entry that claims behavior not present in the release is misleading and invalid as
  release evidence.
- Omitted security-sensitive changes can hide support risk.
- A changelog that mixes unreleased speculation with shipped history is harder to trust.

## Invariants

1. The changelog is readable without repository history.
2. The changelog tracks public releases, not internal commits.
3. Security and breaking changes are explicit.
4. The changelog never outranks the actual release artifacts.

## Tests

- `tests/packaging/test_checksums_sbom_and_provenance.py` — release notes are part of the evidence
  set.
- `tests/packaging/test_build_artifacts.py` — changelog inclusion in the public distribution.

## Open questions

None.

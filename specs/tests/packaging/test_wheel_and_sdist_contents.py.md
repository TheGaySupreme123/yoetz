# tests/packaging/test_wheel_and_sdist_contents.py — artifact member and metadata allowlist

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** pyproject/package metadata, resource and
public-document specs | **Imported by:** packaging/security/release gates

## Purpose

Ensure users receive exactly the reviewed package, executable, metadata, public documents, and
runtime resources—without tests, private/local material, caches, unexpected binaries, or missing
license/security data.

## Public surface

Cases inspect wheel ZIP, sdist tar, distribution metadata, entry points, RECORD, WHEEL, licenses,
readme/security references, `py.typed`, optional extras, Python requirement, and rebuild inputs.

## Behavior

Parse archives without unsafe extraction. Reject traversal, absolute/backslash/control/case-collision,
duplicate, link/device, compression bomb, unexpected mode, or unbounded member. Compare normalized
member paths to exact kind-specific allowlists. Wheel contains one `yoetz_core` package, console
entry `yoetz-core`, declared resources, `py.typed`, dist-info/approved license files, and no tests/
dev config/fixture authorship/private plans/transcripts/Git/editor/cache/temp/source map/database/
WAL/SHM.

Assert metadata project/version/summary/license/authors/public URLs, Python `>=3.14,<3.15`, direct/
optional dependencies, extras, wheel purity/platform statement, and entry target. No local/direct
path/private index or undeclared native Yoetz binary. Sdist includes only files needed to review and
rebuild offline; rebuilding from it yields candidate-equivalent wheel under policy.

## Errors and edge cases

- Unknown file fails; extension/generator directory is not blanket-allowed.
- RECORD hashes/sizes must match and cover each wheel member exactly as standard permits.
- Archive parser limit/incomplete metadata is failure.
- Diagnostics show public-relative member and reason only, not content/local extraction path.

## Invariants

1. Artifact member inventory is explicit and reviewable.
2. Metadata claims match tested runtime/support policy.
3. Sdist can reconstruct without checkout/private source.
4. No unexpected binary or private/debug/test material ships.

## Tests

Mutation fixtures add/remove/duplicate/rename members, alter metadata/RECORD/mode, inject symlink/
native file/private canary, and prove each rejection.

## Open questions

None.

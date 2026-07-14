# scripts/scan_public_boundary.py — public-repository and artifact privacy gate

**Wave:** F | **ADRs:** ADR-004, ADR-007 | **Imports (spec-tree):**
`specs/src/yoetz_core/observability/privacy.md`, `specs/tests/packaging.md` | **Imported by:** PR CI,
security/privacy workflow, tagged release workflow

## Purpose

Block publication of private strategy/business material, local paths, transcripts, credentials,
customer/tenant identifiers, secret canaries, and unexpected files. The scanner applies the public
repository boundary to candidate source, sdist, wheel, metadata, generated schemas/resources,
documentation, and release evidence.

This is a deterministic prevention gate, not a claim that pattern matching can discover every
secret or establish legal/IP clearance. Human review and dependency/license scanners remain
separate gates.

## Public surface

- `ScanTarget`, `BoundaryRule`, `BoundaryFinding`, and `ScanReport` — script-local frozen records;
- `load_rules(path) -> tuple[BoundaryRule, ...]` — parse reviewed public rule config;
- `enumerate_target(target) -> tuple[FileEntry, ...]` — safe sorted file inventory;
- `scan_filename`, `scan_bytes`, `scan_archive_member`, `scan_metadata` — bounded detectors;
- `build_report(...) -> bytes` — canonical redacted JSON summary;
- `main(argv=None) -> int`.

Commands:

```text
uv run --locked python scripts/scan_public_boundary.py --source-tree .
uv run --locked python scripts/scan_public_boundary.py --artifact dist/pkg.whl \
  --artifact dist/pkg.tar.gz --evidence-dir dist/release-evidence
```

CI/release supplies explicit targets. Optional `--canary-file` is accepted only from the CI secret
store path and its bytes are never included in output. Exit `0` means no blocking hit, `1` means a
hit or incomplete scan, `2` bad invocation.

## Behavior

### Target inventory

Source mode uses the release candidate's tracked/exported file manifest, not an unrestricted walk
of the developer home. It also compares against an allowlisted public top-level inventory and
fails on unexpected tracked paths. Artifact mode parses wheel/ZIP and sdist/tar directly without
extracting untrusted paths. Evidence mode accepts only the release-evidence output directory.

Reject absolute/traversing/backslash/control/case-colliding/duplicate/symlink/hardlink/device/archive
member paths before reading contents. Enforce per-file, archive-member, aggregate-byte, member-count,
compression-ratio, and wall-time caps. An unscanned/unsupported member type is a failure, not a skip.

### Reviewed rules

Rules are versioned public data/code and have stable ID, category, severity, scope, detector kind,
bounded pattern/hash, justification, owner role, and optional exact allow exception. Categories
include credential/private-key forms, local home/repository paths, transcript/session markers,
private planning/business document names and canaries, tenant/customer/production identifiers,
debug/build/cache files, unapproved URLs/import/package names, and license/source canaries.

Allow exceptions match exact rule ID + normalized file + bounded digest/line context. There is no
blanket directory, extension, generated-file, binary, or `vendor` exemption. Every exception states
why the public occurrence is intentional and expires/reviews with a release. Secrets and private
keys cannot be allowlisted.

CI injects high-entropy canaries representing local/private source families. The scanner compares
exact bytes and safe encodings without printing them. Rule config contains only public hashes or
synthetic examples, never actual credentials or private text.

### Content scanning

Scan raw bytes and, for bounded known text types, decoded UTF-8. Apply:

- exact canary and safe transformed-form matching;
- high-confidence credential/private-key/token/URI-userinfo patterns shared with privacy tests;
- normalized absolute-home/path patterns without embedding a maintainer's actual home in reports;
- forbidden public-boundary term/path/package inventories;
- source maps, `.env`, key/certificate/database/WAL/SHM, transcript/log/debug/cache/editor and
  unapproved executable/native-binary detection by name and magic bytes;
- wheel metadata, entry points, RECORD, licenses, direct URLs, dependency metadata, and sdist
  manifest checks;
- archive-recursive scanning only for explicitly supported one-level artifact formats.

Binary files are scanned for exact/pattern bytes and validated against the allowlisted kind. An
unknown binary is not treated as clean just because UTF-8 decoding fails.

### Report and failure policy

Each finding records stable rule ID/category/severity, target label, normalized public-relative path,
byte/line location bucket, file digest, and match count. It never records matched bytes, surrounding
text, secret/canary, absolute path, exception text, environment, or archive extraction path.

Any critical/high hit, unexpected file, incomplete scan, parser limit, unsupported member, or
scanner exception blocks. Medium/low findings also block unless an exact reviewed exception exists;
the gate does not gradually normalize warnings. The JSON report is canonical and scans itself
before upload. Human console output is a compact subset.

### Source and artifact stages

PR CI scans the public source candidate plus generated package inventory. Release CI scans clean
export, built sdist/wheels, extracted metadata inventories, SBOM/provenance/checksum/support files,
and finally downloaded published candidates before announcement. Passing source does not waive
artifact scanning; build backends and metadata may introduce leaks.

## Errors and edge cases

- Any I/O/parser/decompression/decode/rule-load error is an incomplete blocking scan.
- Files changing during scan fail; release source/artifacts are immutable inputs.
- Pattern false positives are corrected with narrower reviewed rules/fixtures or exact digest-bound
  exceptions, never `ignore errors`.
- Git ignore status is not a security boundary. Ignored files are not read, but a matching private
  filename/content that enters the public candidate is blocked.
- Scanner logs and CI command lines never expose canaries or secret-store paths.
- This script does not modify, delete, quarantine, upload, or publish anything.

## Invariants

1. No unscanned candidate file/member can coexist with a passing report.
2. Finding output contains no matched content or secret value.
3. Source and built/published artifacts pass independently.
4. Unknown and incomplete states fail closed.
5. Exceptions are exact, reasoned, reviewable, and cannot allow secrets/private keys.
6. The scan performs no network calls and no filesystem writes outside explicit report output.

## Tests

- `specs/tests/unit.md`: every rule positive/negative, redacted findings, exception matching.
- `specs/tests/property.md`: hostile archive paths, compression bombs, arbitrary binary, caps.
- `specs/tests/integration.md`: synthetic source/wheel/sdist/evidence trees seeded with unique
  canaries in filename/content/metadata/encoded forms.
- `specs/tests/packaging.md`: release-artifact allowlist and real candidate scan.
- Security workflow verifies a deliberately seeded canary blocks and never appears in logs/report.

## Open questions

None.

E-008 is the sole central artifact-boundary gate.

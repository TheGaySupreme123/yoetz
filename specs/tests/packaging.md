# tests/packaging/ — reproducible artifact, clean-install, and publication-boundary suite

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-007, ADR-008, ADR-009 |
**Imports (spec-tree):** package/version/resource and service import-boundary specs,
schemas/skills/privacy/fixtures, release support matrix | **Imported by:** release-candidate gate

## Purpose

Verify the artifacts users install rather than a developer checkout. Prove wheel/sdist contents,
resource identity, dependency locking, platform/SQLite support, installation lifecycle, offline
reinstall, public/private boundary, licenses, checksums, and SBOM/provenance evidence.

## Public surface

```text
tests/packaging/
  test_build_artifacts.py
  test_wheel_and_sdist_contents.py
  test_resource_byte_parity.py
  test_service_boundary_imports.py
  test_version_manifest.py
  test_clean_install.py
  test_upgrade_and_backward_read.py
  test_uninstall_and_data_retention.py
  test_offline_reinstall.py
  test_platform_and_sqlite_gate.py
  test_dependency_lock_and_licenses.py
  test_private_boundary_and_secret_scan.py
  test_privacy_docs_and_resources.py
  test_checksums_sbom_and_provenance.py
```

Tests consume artifacts from a clean build directory produced by pinned `uv`/`uv_build`.
They never import the source tree through cwd/PYTHONPATH.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
tests/packaging/test_build_artifacts.py
tests/packaging/test_checksums_sbom_and_provenance.py
tests/packaging/test_clean_install.py
tests/packaging/test_dependency_lock_and_licenses.py
tests/packaging/test_offline_reinstall.py
tests/packaging/test_platform_and_sqlite_gate.py
tests/packaging/test_privacy_docs_and_resources.py
tests/packaging/test_private_boundary_and_secret_scan.py
tests/packaging/test_resource_byte_parity.py
tests/packaging/test_service_boundary_imports.py
tests/packaging/test_uninstall_and_data_retention.py
tests/packaging/test_upgrade_and_backward_read.py
tests/packaging/test_version_manifest.py
tests/packaging/test_wheel_and_sdist_contents.py
```

## Behavior

### Build and contents

- Build sdist and pure-Python Yoetz wheel twice from clean normalized checkouts with fixed documented
  build environment; compare file lists and reproducibility metadata/digests according to ADR-007.
- Assert distribution/executable `yoetz`, version metadata, Python
  `>=3.14,<3.15`, optional extras, entry point, `py.typed`, license/readme/security
  metadata, and no undeclared native binary in the Yoetz wheel.
- Enforce allowlisted files. Exclude tests, development caches, private planning sources,
  transcripts, Git history, credentials, editor state, temp files, fixture authoring sources, and
  unknown top-level packages.
- Inspect sdist can rebuild wheel offline from the captured wheelhouse/lock inputs.

### Resource parity

Root reviewed schemas, migrations, skill/references, policy data, compatibility fixtures, and support
manifest equal packaged/installed bytes, sizes, paths, and SHA-256. Runtime manifest digest equals the
build manifest. Missing/extra/duplicate/path-traversal/case-collision and one-byte corruption cases
fail import/write startup as specified.

The root `PRIVACY.md`, privacy/service protocol documents, four privacy and five service schemas,
schema manifest, and privacy public-claim mappings are required source/sdist artifacts. The 52
schema artifacts and schema manifest have byte-identical installed mirrors; the eight privacy
fixtures remain public test/sdist-only and must not appear in the wheel resource set.

### Clean installation

On fresh advertised macOS arm64 and manylinux_2_28 x86-64 profiles:

1. install with the exact documented `uv tool install --managed-python --python 3.14.6` path;
2. run import, help, `version --json`, strict-local vertical slice, MCP initialize/tools/list,
   and deterministic receipt outside checkout;
3. verify exact APSW wheel/SQLite source ID/options and write support;
4. install each optional extra and exercise only its named capability;
5. ensure no network is needed at runtime for strict-local after installation.

Import-boundary tests prove ordinary CLI/MCP/client imports cannot import concrete vault, storage,
provider, privacy-gateway, or trusted-human-control composition. Only `service run` reaches the
ready service composition; installed clients connect through the authenticated local protocol and
report `service_unavailable|vault_locked` without starting a hidden runtime.

Unsupported Python patch/distribution/OS/ABI can inspect version/read-only and must fail writes with
an honest bounded limitation.

### Upgrade/uninstall/offline

Install every supported prior artifact with old fixture bundle, create data, upgrade to candidate,
run migration on a copy/normal path, replay/check/receipt, and preserve canonical event bytes. Failed
upgrade leaves original data/restoration instructions intact.

Uninstall removes executable/package/integration only. It never deletes app-data bundles, keys, or
user-modified installed skill without a separate explicit action. Reinstall reattaches preserved data
according to catalog/commitment rules.

Capture all required distributions and hashes, then repeat clean install/reinstall with network
denied. Missing platform wheel fails before partial installation and does not compile/download an
unreviewed SQLite variant.

### Dependency, license, supply chain

- `uv.lock` resolves exactly for each target; direct/transitive versions and hashes match
  release manifest.
- APSW/cryptography/native-wheel source identities and licenses meet allowlist.
- Generate CycloneDX SBOM from locked artifact set; every installed distribution appears exactly
  once with version/hash/license where available.
- Generate SHA-256 checksum file over all release artifacts/SBOM/support evidence and verify using the
  documented command.
- Record build tool/runtime/source revision and CI identity as provenance. Do not claim signing until
  an end-user verification command is documented/tested.

### Public/private publication boundary

Scan filenames and bytes in source distribution, wheel, SBOM, metadata, schemas, skill, fixtures,
help/version output for:

- private package/import names and known Yoetz App/Agent internal ontology;
- local user/home/repository paths, ignored private planning documents, transcripts/session IDs;
- credentials, production URLs/tenant/customer identifiers, secrets/canaries;
- incompatible/copied license headers or unapproved source;
- build/debug artifacts.

The scan uses a reviewed allowlist with reason/owner; no blanket path/file exclusion. A hit blocks
publication.

## Errors and edge cases

- Tests use installed console scripts resolved inside isolated tool environments, never globally.
- Build timestamps/ZIP metadata are normalized only per documented reproducible-build policy; tests
  cannot ignore arbitrary file differences.
- Platform jobs use release artifact downloads from the candidate set, not `pip install latest`.
- External package-index outage does not alter artifact identity; offline wheelhouse is the release
  evidence.
- Dirty working tree may be analyzed, but a release build requires clean reviewed source identity.

## Invariants

1. Users receive exactly the reviewed resources/contracts.
2. Strict-local works from installed artifacts with no checkout or network.
3. Exact SQLite/runtime/platform support is measured and reported.
4. Upgrade preserves canonical history; uninstall preserves user data.
5. No private/business/secret material crosses the public artifact boundary.
6. Checksums/SBOM/provenance describe the shipped bytes.

## Tests

```bash
uv build --no-sources
uv run --locked pytest tests/packaging -q --timeout=600
```

Release evidence is retained per target and linked from the support/claim manifest. All advertised
target jobs must pass; narrowing support is allowed, waiving artifact integrity is not.

## Open questions

None.

E-008 is the sole central reproducibility gate.

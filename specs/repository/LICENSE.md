# LICENSE — Apache License 2.0 grant

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** `tests/packaging.md`
**Imported by:** source distributions, wheels, release notices, and downstream users

## Purpose

This file is the canonical legal grant that accompanies the public `yoetz` release. It defines
what downstream users may do with the code, under what conditions, and with what disclaimers.

## Public surface

The founder selected **Apache License 2.0** for Yoetz. The file must contain:

- the unmodified full Apache License, Version 2.0 text;
- the SPDX identifier `Apache-2.0` in release metadata;
- no project-specific prose inserted into or appended to the canonical license text;
- no fabricated project-wide copyright-holder declaration in package metadata or the README;
- no private addenda that are not part of the public grant.

## Behavior

The LICENSE file must be copied into source distributions and wheels exactly as reviewed. Its text
must agree with the package metadata and with any license references in the README or packaging
manifest.

The license file is part of the public artifact boundary:

- it must be shipped with the installable distribution;
- it must be discoverable without requiring repository history;
- it must not rely on local worktree state or private documents to interpret the grant;
- it must not be rewritten by build steps.

If the project uses third-party components with separate notices, those notices belong in the
approved notice mechanism for the release; they do not replace the project license text.
For v0.1 that mechanism is the generated dependency-license/SBOM release appendix; no separate
root `NOTICE` file is introduced.

## Errors and edge cases

- A missing license file blocks release packaging.
- A license mismatch between metadata and file text blocks publication.
- Private commentary appended to the license text would invalidate the public grant boundary.

## Invariants

1. The license text is stable across source, sdist, and wheel.
2. The license file is public and complete.
3. The license grant does not depend on private repository content.
4. Downstream users can evaluate rights without consulting implementation docs.

## Tests

- `tests/packaging/test_dependency_lock_and_licenses.py` — license presence and allowlist checks.
- `tests/packaging/test_wheel_and_sdist_contents.py` — license file inclusion and byte parity.
- `tests/packaging/test_private_boundary_and_secret_scan.py` — no private notes embedded in public
  legal text.

## Open questions

None. F-001 is resolved: use the official license text and SPDX expression without inventing a
project-wide holder notice.

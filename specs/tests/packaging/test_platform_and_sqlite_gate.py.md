# tests/packaging/test_platform_and_sqlite_gate.py — artifact/runtime/SQLite support enforcement

**Wave:** C/F | **ADRs:** ADR-003, ADR-007 | **Imports (spec-tree):** connection/diagnostics/version
and platform support specs | **Imported by:** installation and release support matrix

## Purpose

Verify candidate artifacts admit writes only on exact certified Python/OS/CPU/ABI/filesystem/APSW/
SQLite source-option combinations and report unsupported/unsafe combinations honestly.

## Public surface

Positive cells: advertised macOS arm64 and manylinux/glibc x86-64 artifacts. Negative cells mutate
Python patch/distribution, OS/arch/ABI, APSW/SQLite version/source ID/compile options/thread mode,
filesystem safety, WAL behavior, package tag and support-manifest digest.

## Behavior

Install the exact target artifact, independently probe platform tags/runtime and SQLite identity,
then compare `version --json` and startup diagnostic verdict. Positive cell opens fresh/retained
bundle, applies PRAGMAs, writes/checkpoints/backups/reopens, and passes bounded multiprocess single-
writer tests. Report exact source-ID hash/options verdict and support manifest.

For each negative mutation, version/read-only inspect may work per policy, but any create/attach/write/
migrate/checkpoint attempt fails before mutation with bounded unsupported/unsafe reason. Version
string alone cannot override a mismatched source ID/options. Shared/network filesystem is rejected.

## Errors and edge cases

- Runner labels are not oracle; observed platform/runtime identity is.
- Cannot monkeypatch only reported strings; test uses real alternate fixture builds or diagnostic
  injection whose production denial is proven.
- Unknown future patch/platform is untested, not presumed compatible.
- No fallback to stdlib SQLite or source compilation.

## Invariants

1. Writable support is an exact artifact/runtime/platform/filesystem cell.
2. Unsafe or unknown identity fails before durable mutation.
3. SQLite source/options are measured, not inferred from version.
4. Public report and actual gate agree.

## Tests

Run positives on certified release runners and a maintained denied-fixture matrix; evidence captures
structural identity/digests and write/reopen verdict.

## Open questions

None.

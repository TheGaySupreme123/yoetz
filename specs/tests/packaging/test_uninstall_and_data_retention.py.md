# tests/packaging/test_uninstall_and_data_retention.py — package removal without user-data loss

**Wave:** F | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):** config paths, key/
catalog/bundle, skill integration and packaging specs | **Imported by:** install lifecycle claim

## Purpose

Prove uninstall removes package-managed code/entry points/integration while preserving user app-data,
bundles, backups, external key entries, and modified repository skill copies unless separately and
explicitly authorized.

## Public surface

Cases: base/extra uninstall, tool-environment deletion, identical integrated skill, locally modified
skill, active/preserved bundle, machine-bound/passphrase key modes, reinstall/reattach, and explicit
integration removal command before uninstall.

## Behavior

Clean-install candidate, create known workflow/receipt, integrate skill into isolated repository,
modify one copy, record package-managed and user-data inventories/digests. Invoke documented package
manager uninstall only. Assert executable/import/distribution files disappear while app-data/catalog/
bundle/objects/backups and external key/recovery state remain byte-identical; no product process runs.

Package uninstall hooks do not run arbitrary data deletion. An unchanged integration may remain or
be removed only through explicit prior `integrate remove`; modified copy is always preserved with
status. Reinstall exact candidate offline, reattach, status/replay/receipt and compare history.

## Errors and edge cases

- Test uses disposable HOME/key namespace and never real user data.
- Package-manager cache removal is not user-data deletion and is recorded separately.
- Missing retained key yields honest inaccessible bundle; uninstall must not have deleted it.
- Uninstall/reinstall failure cannot trigger recursive cleanup.

## Invariants

1. Package removal never deletes user ledger/object/backup/key data.
2. Modified integrated files remain user-owned.
3. Reinstall can rediscover/reattach preserved compatible data.
4. Test cleanup is separate and scoped to its temp root.

## Tests

Run on all advertised installers/platforms, comparing before/after inventories and replay digests;
negative canary paths prove no broad deletion.

## Open questions

None.

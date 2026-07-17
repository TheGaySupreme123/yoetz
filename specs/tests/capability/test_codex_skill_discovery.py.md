# tests/capability/test_codex_skill_discovery.py — Codex skill discovery and safe integration

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** skill/references/resource
manifest/integration command specs and capability evidence | **Imported by:** skill support matrix

## Purpose

Prove Codex finds the canonical installed skill under claimed triggers and that integration preview,
consent, copy, modification protection, status, and removal preserve user-owned repository files.

## Public surface

Cases: explicit `$yoetz`; material-task implicit trigger if claimed; trivial non-trigger;
canonical source/wheel/install parity; preview/decline/accept; existing identical/modified copy;
compatible/incompatible skill-MCP versions; exact managed-path resolution; duplicate `yoetz` names
across loaded roots; status/remove.

## Behavior

Use isolated trusted/untrusted synthetic repositories. Verify explicit discovery and ten-step
workflow guidance against public observable actions, not hidden reasoning. If implicit activation is
claimed, run positive material and negative trivial fixtures; otherwise record explicit-only.
Query app-server `skills/list` or its exact tested successor and require one error-free `yoetz`
entry resolving to the managed project path. Seed same-name ancestor, user, and plugin skills;
ambiguity fails the capability cell and is never resolved by undocumented load order.

Integration first renders deterministic diff and requires consent. Accepted copy equals canonical
resource bytes/references/compatibility manifest. Identical copy is no-op; locally modified copy is
never silently overwritten/removed. Incompatible version yields bounded guidance and no false live
claim. Removal deletes only an unchanged tool-installed copy.

## Errors and edge cases

- Tests never alter real `.agents`, repository, or global skill directories.
- Symlink/path escape/case collision/permission denial fails before mutation.
- Codex wording is classified against frozen required/forbidden claims; raw transcript stays private.
- Source/package/install digest mismatch fails.

## Invariants

1. One reviewed skill byte set underlies source, wheel, and installed copy.
2. User modification requires explicit conflict resolution.
3. Discovery claims are exact-version observations.
4. Skill never describes checks/receipts not performed.

## Tests

Emit evidence for trigger outcome, resource digests, diff/consent action, file-before/after digest,
and limitation codes across supported Codex cells.

## Open questions

None.

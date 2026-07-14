# src/yoetz_core/adapters/importers/__init__.py — side-effect-free importer package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** importer module specs in
`adapters/importers/` | **Imported by:** importer tests and explicit submodule imports

## Purpose

Mark `yoetz_core.adapters.importers` as a regular package boundary for importer-specific modules.

## Public surface

- No reexports. Import importer modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package performs no file reads, fixture discovery, network work, or parser setup.
It does not import importer modules eagerly and does not create default loader instances.

## Errors and edge cases

- Any import-time scan of the fixture corpus or filesystem is forbidden.
- A marker that silently constructs importer state is a contract breach.

## Invariants

1. Import is inert and explicit.
2. No hidden loader defaults are created.
3. Importer modules remain individually addressable.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.

# src/yoetz/adapters/control/__init__.py — local control adapter package marker

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** none | **Imported by:**
control adapter modules and import tests

## Purpose

Marks the local-control adapter package without binding a socket or importing platform code.

## Public surface

No runtime public API. `__all__` is empty.

## Behavior

Import is side-effect free; callers import `unix_socket` explicitly.

## Errors and edge cases

Missing platform capabilities do not fail package import.

## Invariants

1. No endpoint, event loop, peer identity, or global listener is created at import.
2. No confidential secret client is re-exported.

## Tests

- `tests/packaging/test_service_boundary_imports.py` covers side-effect-free import.

## Open questions

None.

# src/yoetz_core/adapters/privacy/__init__.py — privacy adapter package boundary

**Wave:** C–E | **ADRs:** ADR-008, ADR-009 | **Imports (spec-tree):** none | **Imported by:**
runtime composition only

## Purpose

Mark the package containing local deterministic privacy enforcement, catalog-backed policy/audit
state, and the sole outbound gateway. Importing it performs no I/O and grants no network authority.

## Public surface

No re-exports. Composition imports concrete adapters from their owning modules.

## Behavior

The package initializer contains only its docstring and `__all__: tuple[str, ...] = ()`. It never
imports provider SDKs, opens policy/audit storage, probes the keyring, or constructs a transport.

## Errors and edge cases

None; import is side-effect free.

## Invariants

1. Importing the package cannot create network or disclosure capability.
2. Concrete privacy authority remains explicit at runtime composition.

## Tests

Import-side-effect and package-boundary tests cover this module.

## Open questions

None.


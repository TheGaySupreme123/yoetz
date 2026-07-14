# src/yoetz_core/adapters/providers/__init__.py — side-effect-free provider package marker

**Wave:** F | **ADRs:** ADR-006, ADR-007, ADR-009 | **Imports (spec-tree):** provider adapter module specs in
`adapters/providers/` | **Imported by:** provider-selection tests and explicit submodule imports

## Purpose

Mark `yoetz_core.adapters.providers` as the package boundary for provider implementations owned
exclusively by the privacy gateway, including external, exact AF_UNIX local-model, and fake adapters.
The package-level adapter input contract is the closed `ApprovedProviderCase` union: external
`ApprovedOutboundCase` or local `ApprovedLocalDisclosureCase`.

## Public surface

- No reexports. Import provider modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not contact a model provider, inspect credentials, resolve endpoints, or
allocate network/local-socket clients. Selection remains an explicit gateway composition decision;
application/check code cannot import an adapter.

## Errors and edge cases

- Any import-time provider client construction is forbidden.
- The marker must not imply that semantic review is configured.

## Invariants

1. Import is inert.
2. No provider network access happens at package import time.
3. Explicit submodule imports own the behavior.
4. Concrete evaluators accept only `ApprovedProviderCase` and are reachable only through the
   policy-enforcing gateway. External adapters narrow to `ApprovedOutboundCase`; the AF_UNIX local
   adapter narrows to `ApprovedLocalDisclosureCase`; neither variant can be reinterpreted as the
   other.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.

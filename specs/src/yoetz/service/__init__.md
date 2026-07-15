# src/yoetz/service/__init__.py — persistent local service package boundary

**Wave:** C/D | **ADRs:** ADR-001, ADR-004, ADR-008 | **Imports (spec-tree):** none |
**Imported by:** service modules and package import tests

## Purpose

Marks the package containing the trusted per-user service. Importing it must not start a daemon,
touch the keyring, bind a socket, load configuration, open storage, or register signal/session
hooks. The package boundary makes service-only modules distinguishable from CLI/MCP client code.

## Public surface

The package initializer exports only type-checking-safe names `ServiceState` and
`ServiceClient`. Runtime users import concrete daemon, lifecycle, vault, and transport components
from their owner modules. `__all__` is exact and contains no global service singleton.

## Behavior

Import is deterministic and side-effect free. Type-only exports must avoid importing keyring,
cryptography, SQLite, provider SDKs, or platform service-manager modules at runtime. Version and
feature discovery remain owned by `yoetz.version`.

## Errors and edge cases

Optional dependencies absent at import time do not make this package import fail. Any accidental
adapter construction or event-loop lookup during import is a packaging defect.

## Invariants

1. Importing the package performs no I/O and creates no process-global service state.
2. No key, secret, endpoint, configuration, or application object is exported here.
3. CLI/MCP code can import client types without importing service-only concrete adapters.

## Tests

- `tests/packaging/test_service_boundary_imports.py` imports this package with every optional
  dependency absent and asserts zero filesystem, environment, socket, logging, and keyring access.

## Open questions

None.

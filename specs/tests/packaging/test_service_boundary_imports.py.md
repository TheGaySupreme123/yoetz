# tests/packaging/test_service_boundary_imports.py — installed trust-boundary import suite

**Wave:** F | **ADRs:** ADR-008 | **Imports (spec-tree):** service/client/daemon/MCP/CLI package specs | **Imported by:** test runner

## Purpose

Prove installed ordinary client and MCP graphs cannot import trusted vault/storage/provider/application composition.

## Public surface

Clean-interpreter import graph, optional-dependency absence, side-effect, and forbidden-symbol tests.

## Behavior

Import client/MCP/CLI normal modules under hooks that fail keyring/SQLite/cryptography/provider/filesystem access; import daemon separately and verify composition owner.

## Errors and edge cases

Lazy/dynamic imports, re-exports, TYPE_CHECKING behavior, missing optionals, fork.

## Invariants

1. Confidential client is unreachable from MCP/ordinary client exports.
2. Only daemon imports ready composition in production.
3. `HumanControlClient` and `ConfidentialSecretClient` are reachable only from trusted CLI helper
   modules and absent from MCP/ordinary service-client import graphs.

## Tests

This file is the executable owner on wheel and sdist installs.

## Open questions

None.

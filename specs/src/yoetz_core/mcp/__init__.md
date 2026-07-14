# src/yoetz_core/mcp/__init__.py — side-effect-free MCP package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** MCP module specs in `mcp/` |
**Imported by:** MCP tests and explicit submodule imports

## Purpose

Mark `yoetz_core.mcp` as the package boundary for the stdio server, error mapping, and summaries.

## Public surface

- No reexports. Import MCP modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not start a server, bind stdio, or emit protocol frames. It is a
package marker only.

## Errors and edge cases

- Any import-time protocol startup is forbidden.
- The marker must not create a listening socket or worker thread.

## Invariants

1. Import is inert.
2. MCP startup remains explicit.
3. No transport side effect occurs at package import time.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.

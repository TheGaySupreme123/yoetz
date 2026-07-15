# src/yoetz/__main__.py — `python -m yoetz` entry point

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** `cli/app.md` | **Imported by:** Python's
module runner only

## Purpose

Make `python -m yoetz` behaviorally identical to the installed `yoetz` console script,
without introducing another CLI implementation or runtime-construction path.

## Public surface

This file exports no supported library API. Its executable behavior is:

```text
from yoetz.cli.app import main
if __name__ == "__main__":
    main()
```

The final implementation may include type-checking annotations but no additional branch, wrapper,
argument rewriting, exception catch, or event-loop construction.

## Behavior

Import `main` only when this module is executed. Delegate the original `sys.argv`, stdin, stdout,
stderr, environment, signals, and current working directory unchanged. `cli.app.main` owns Typer
startup, the one `anyio.run` bridge, JSON/human rendering, and exit-code mapping.

Importing `yoetz.__main__` as a module does not invoke `main`.

## Errors and edge cases

- `SystemExit` and `KeyboardInterrupt` behavior is exactly the CLI contract; this file does not
  translate or suppress either.
- Broken pipe, invalid arguments, and startup failures remain owned by `cli/app.md` and
  `cli/exits.md`.
- MCP stdout purity is unchanged when invoked as `python -m yoetz mcp serve`.

## Invariants

1. Console-script and module invocation produce byte-identical JSON/stdout/stderr and equal exits
   for equal inputs.
2. There is exactly one CLI application and one async bridge.
3. No package version or command behavior is hardcoded here.

## Tests

- `specs/tests/subprocess.md`: snapshot every command through both invocation forms and compare
  stdout, stderr, and exit code byte-for-byte after normalizing only executable path in help usage.
- `specs/tests/packaging.md`: run from a clean installed wheel outside the checkout.

## Open questions

None.

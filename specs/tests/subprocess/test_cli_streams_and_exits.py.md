# tests/subprocess/test_cli_streams_and_exits.py — CLI stdout/stderr and exit-code contract

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** CLI render/exit specs,
`specs/tests/subprocess/helpers/child.py.md` | **Imported by:** installed-artifact gates

## Purpose

Lock byte-channel separation and public exits independently of command semantics. Shell automation
must distinguish success, invalid input, conflicts, unavailable/busy/unsafe states, internal fence,
cancellation, and broken delivery without parsing tracebacks or user content.

## Public surface

Parameterized cases cover exits `0,2,10,11,20,30,40,70,130`, human vs `--json`, warning/finding,
TTY/non-TTY, broken stdout pipe, stderr pipe closure, SIGINT, and unexpected internal exception.
The mapping parameterization enumerates every registered `PublicErrorCode`, not one representative
per exit family, and asserts set equality with `PUBLIC_EXIT_CODES`.

## Behavior

Use scripted adapters/fault hooks to produce one canonical cause per exit. Assert stdout contains
exactly one complete result in success/application-error JSON modes and stderr only bounded
diagnostic lines. Human findings remain stdout result content; warnings never change exit. Invalid
CLI parsing exits `2`; operation conflicts/unavailable/busy/storage/internal/cancel map to their
frozen codes. Exit `30` is exercised only by a direct provider setup/probe/support operation with
no completed deterministic result; every completed deterministic check with a semantic gap exits
`0` and returns `incomplete_check`. SIGINT yields `130` without traceback.
Read-only status with a future frontier exercises `INVALID_REQUEST`/`2`, never
`FRONTIER_CONFLICT`/`10`.

Close the reader before/during output to exercise broken pipe. Durable success before response loss
is not reclassified as rollback; stderr states outcome unknown without payload, and same-request
retry proves result. Capture raw bytes and scan for seeded title/path/secret/exception canaries.

## Errors and edge cases

- Partial JSON, two JSON documents, ANSI/color in non-TTY, pretty exceptions, logging on stdout, or
  arbitrary exception text fails.
- stderr closure must not corrupt stdout or change durable outcome.
- OS-specific broken-pipe numeric details are normalized only to the public exit/reason.

## Invariants

1. stdout is result data; stderr is bounded diagnostics.
2. Exit code depends on public outcome, not presence/count of findings.
3. Delivery failure never rewrites durable truth.
4. User-controlled bytes and tracebacks appear on neither channel.

## Tests

Golden byte fixtures cover each mode/code and are run on every advertised platform. The test also
verifies `python -m` parity through the dedicated parity file.

## Open questions

None.

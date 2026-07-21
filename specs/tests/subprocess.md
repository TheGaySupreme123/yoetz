# tests/subprocess/ — CLI/MCP process, signal, framing, and crash-boundary suite

**Wave:** C–F | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-005, ADR-007, ADR-008, ADR-009 |
**Imports (spec-tree):** CLI, MCP, local service/control/unlock, privacy human control, bounded
stdio, storage/object specs, fixtures | **Imported by:** installed-artifact release gates

## Purpose

Prove behavior that cannot be trusted from in-process mocks: executable entry points, stdout/stderr
purity, raw descriptor framing/backpressure, process ownership, signals/cancellation, partial writes,
ambiguous client outcomes, abrupt termination, and restart/retry.

All child processes run from the built/installed artifact against isolated temp HOME/app-data and
strict resource caps.

## Public surface

```text
tests/subprocess/
  test_cli_invocations.py
  test_cli_streams_and_exits.py
  test_module_entrypoint_parity.py
  test_mcp_initialize_and_tools.py
  test_mcp_stdio_frames.py
  test_mcp_stdout_purity.py
  test_mcp_backpressure_and_partial_write.py
  test_mcp_service_bridge.py
  test_signals_and_cancellation.py
  test_service_lock_and_confidential_unlock.py
  test_service_daemon_lifecycle.py
  test_service_secret_boundary.py
  test_service_unlock_boundary.py
  test_setup_wizard_cli.py
  test_privacy_human_control.py
  test_process_owner_fencing.py
  test_kill_matrix.py
  test_reopen_retry_replay.py
  helpers/
    child.py
    frame_driver.py
    fault_controller.py
```

The parent driver uses OS pipes/process groups and byte APIs. It never parses a stream before saving
the exact bytes/digest needed for failure evidence.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
tests/subprocess/helpers/child.py
tests/subprocess/helpers/fault_controller.py
tests/subprocess/helpers/frame_driver.py
tests/subprocess/test_cli_invocations.py
tests/subprocess/test_cli_streams_and_exits.py
tests/subprocess/test_kill_matrix.py
tests/subprocess/test_mcp_backpressure_and_partial_write.py
tests/subprocess/test_mcp_initialize_and_tools.py
tests/subprocess/test_mcp_service_bridge.py
tests/subprocess/test_mcp_stdio_frames.py
tests/subprocess/test_mcp_stdout_purity.py
tests/subprocess/test_module_entrypoint_parity.py
tests/subprocess/test_privacy_human_control.py
tests/subprocess/test_process_owner_fencing.py
tests/subprocess/test_reopen_retry_replay.py
tests/subprocess/test_service_daemon_lifecycle.py
tests/subprocess/test_service_lock_and_confidential_unlock.py
tests/subprocess/test_service_secret_boundary.py
tests/subprocess/test_service_unlock_boundary.py
tests/subprocess/test_setup_wizard_cli.py
tests/subprocess/test_signals_and_cancellation.py
```

## Behavior

### CLI executable contract

For console script and `python -m yoetz`, snapshot:

- root/per-command help, version, invalid/missing/unknown flags;
- six operations in human and `--json` modes;
- `--input PATH`, stdin `-`, non-TTY behavior, oversized/invalid/duplicate-key input;
- support commands import/review/backup/restore/migrate/version/integrate;
- stdout result vs stderr diagnostic separation and exits 0/2/10/11/20/30/40/70/130;
- findings do not change a successful operation exit;
- broken-pipe behavior and no traceback/pretty-exception/user-input echo.

Normalize only invocation path in help text. JSON/canonical output and stderr lines are otherwise
byte assertions.

### MCP lifecycle and tool calls

Drive raw JSON-RPC initialize/negotiation, initialized notification, tools/list, all six tools,
unknown tool/method, invalid lifecycle order, cancellation, EOF, and orderly shutdown. Validate
input/output schemas and `isError` semantics. No frame may be emitted before a complete valid
request except protocol lifecycle output.

### Bounded stdio golden matrix

Each case runs in a fresh child and asserts forwarded messages, exact output frames, exit, stderr,
peak buffer, and synchronization:

1. empty line and whitespace-only frame;
2. smallest valid frame;
3. exactly 1 MiB payload excluding LF;
4. cap plus one byte, both with LF present and delayed;
5. multiple frames in one read and frame split across every boundary;
6. `os.read` chunks never exceeding 64 KiB;
7. invalid UTF-8 at beginning/middle/end;
8. BOM, NUL, duplicate key, malformed JSON, float/unsafe integer as applicable;
9. CRLF/extra LF policy exactly as transport spec;
10. EOF after complete frame and partial EOF;
11. oversized unterminated input: no prefix forwarded and bounded termination/recovery;
12. malformed recoverable frame followed by valid frame;
13. parse error with unrecoverable ID emits the fixed `id:null` frame or certified orderly
    termination policy;
14. partial `os.write` of 1 byte/short chunks, EINTR/readiness delay;
15. slow/non-reading client proves zero-capacity backpressure and bounded memory;
16. broken pipe, cancellation, task failure, injected stdout noise.

The sole stdout writer appends exactly one LF and loops until all bytes are written. SDK/application
diagnostics may appear only as allowlisted structural stderr records. Exception messages,
`exc_info`, and tracebacks appear on neither stream and are not captured in any child-owned file or
diagnostic artifact.

### Signals and cancellation

Send SIGINT/SIGTERM during idle, validation, object staging, before transaction, cancellation-shielded
commit, provider fake wait, stdout response, checkpoint, and shutdown. Expected exits and durable
state follow the contract: smallest commit section may finish; response loss makes outcome unknown;
retry same request resolves without duplicate; no new work starts during shutdown.

MCP cancellation re-raises the cancellation class through exception fences and does not become
`INTERNAL_ERROR`. Application operation cancellation records only durable phase state allowed
by its state machine.

### Owner fencing

Start one service and race a second daemon for the same installation. Exactly one acquires
singleton/catalog/bundle generations; the loser touches no key/writer. Concurrent MCP/CLI clients
share the winner. Kill it, race two successors, and assert one generation advance/winner. PID
reuse/diagnostic metadata never authorize writes.

### Service, confidential ingress, and client separation

Start the service once and prove CLI plus the stdio MCP bridge attach as ordinary clients; neither
opens the bundle, imports concrete key/provider composition, or autostarts a hidden runtime. Exercise
absent, locked, ready, response-loss, reconnect, and successor-generation behavior. The ordinary
control registry must have no unlock, credential, privacy-decision, proof, or arbitrary-method
branch. Unlock uses only the foreground `/dev/tty` no-echo helper and the separately typed
peer-authenticated ingress; pipes/stdin/argv/env/config/MCP fail before secret read. Privacy
widening and per-request disclosure approval require the separate local-human control ceremony,
with no reusable token crossing back to the CLI or MCP process.

Privacy CLI subprocess snapshots cover `setup|show|propose|tighten` and receipt inspection, all five
recipe expansions, all thirteen typed answers, editable review, exact eligible states, known-broad/
unknown/stale recommendation rejection, and `recommendation_unavailable`. A PTY is used only for the intentional trusted-control
ceremony. After one assisted-policy commit, ordinary check/retry/challenge/respond/recheck paths
produce no prompt; `confirm_every_request` alone prompts once per physical attempt. Human output
shows the reviewer's direct explanation and requested next step through the existing finding
surface, while MCP exposes no policy-decision tool.

### Durability kill matrix

Fault controller deterministically stops the child at all 16 semantic boundaries:

1. before object creation;
2. after partial ciphertext;
3. after file fsync before rename;
4. after rename before directory fsync;
5. after durable object before `BEGIN IMMEDIATE`;
6. after sequence allocation before event insert;
7. after event insert before projection update;
8. after projection update before operation row;
9. immediately before commit;
10. immediately after commit before response serialization;
11. during MCP stdout response;
12. during PASSIVE/FULL checkpoint;
13. during backup object copy/manifest finalization;
14. during migration/restore catalog switch;
15. after provider fake response before attempt persistence;
16. after semantic persistence before freshness validation.

After each kill, reopen only through normal startup validation, capture last durable frontier, retry
same operation, replay from empty projection, and assert no acknowledged missing event/object, no
duplicate effect/partial batch, valid chains, reference-equal projection, and no second semantic
steering. Tests distinguish pre-commit not-applied from post-commit unknown-to-client.

### Resource safety

Before running, detect any existing suite-owned child by unique temp-root marker; never kill by broad
process name. Each case caps wall time, children, descriptors, memory, output bytes, and disk/WAL
growth. Parent kills only its own process group on timeout and preserves bounded diagnostics.

## Errors and edge cases

- Platform-specific signal/pipe behavior is supported only on advertised macOS arm64 and glibc
  Linux x86-64; an advertised platform cannot skip its certified matrix.
- Fault markers are test-only injected semantic hooks removed/disabled in release runtime; tests
  prove production config cannot activate them.
- Output drivers avoid deadlock by concurrently draining bounded stdout/stderr while retaining exact
  bytes.
- A timeout is a failing inconclusive test, not evidence the product handled the case.

## Invariants

1. MCP stdout contains only valid protocol frames.
2. Process death around commit is resolved by durable idempotency, never timing guesses.
3. Only current owner generation can mutate/checkpoint.
4. Every child is isolated, bounded, and attributable to its parent test.
5. Console/module invocation are behaviorally identical.
6. The kill matrix exercises release artifact paths, not a simplified test repository.

## Tests

```bash
uv run --locked pytest tests/subprocess -m "not kill_matrix" -q --timeout=180
uv run --locked pytest tests/subprocess -m kill_matrix -q --timeout=600
```

The full kill matrix is supervised, serial per bundle, parallel only across isolated workers within
precommitted machine limits.

## Open questions

None.

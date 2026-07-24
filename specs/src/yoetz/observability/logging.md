# src/yoetz/observability/logging.py — structured allowlisted stderr logging

**Wave:** D | **ADRs:** ADR-005, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`specs/src/yoetz/observability/privacy.md`, `specs/src/yoetz/config/models.md`,
`specs/src/yoetz/ports/clock.md` |
**Imported by:** `specs/src/yoetz/mcp/server.md`, `specs/src/yoetz/cli/app.md`,
`specs/src/yoetz/application/service.md`, all adapter specs

## Purpose

Provides the only logging surface in the process: a structured adapter over stdlib `logging`
that can emit exactly the allowlisted fields to stderr, filters SDK records at every level, and
guarantees MCP stdout is never touched. Without it, one stray `logging.error(payload)` in a
dependency could leak user plaintext or corrupt the MCP protocol stream.

## Public surface

- `configure_logging(config: LoggingConfig, mode: LogMode, *, clock: ClockPort | None = None) ->
  None` — installs handlers/filters once per process, before any adapter is constructed. Production
  composition omits `clock` and receives the exact-millisecond UTC system-clock adapter; tests
  inject a conforming `ClockPort` so timestamp bytes never depend on ambient time.
- `class LogMode(Enum)` — registered closed values `service`, `cli`, `mcp_stdio`, and
  `confidential_helper`.
- `get_logger(component: str) -> StructuredLogger` — `component` is a bounded reviewed constant
  (module identity), never user input.
- `class StructuredLogger` — methods `debug/info/warning/error(operation: str, **fields)`
  accepting **only** allowlisted field names (below).
- `record_unexpected_exception_without_raising(exc: BaseException) -> str` — emits one bounded
  structural correlation record, returns a new `err_` correlation ID, and never formats/captures
  the exception or raises (name fixed by `specs/src/yoetz/cli/exits.md` and
  `specs/src/yoetz/mcp/errors.md`).
- `record_fatal_exception_without_raising(exc: BaseException) -> None` — emits the same bounded
  structural identity for the exit-70 process boundary; never formats/captures the exception and
  never raises, including from the last-resort MCP boundary owned by
  `specs/src/yoetz/mcp/server.md`.

## Behavior

### Allowlisted fields (exact and closed)

```text
timestamp, level, component, operation, correlation_id,
session_id_hash, request_id, duration_ms, outcome, reason,
engine_version, policy_version, sqlite_source_id_hash
```

- `timestamp`: RFC 3339 UTC, exactly three fractional digits, `Z` (same rendering as protocol
  timestamps; injected `ClockPort` in tests).
- `level`: `debug|info|warning|error`.
- `component`/`operation`/`outcome`: bounded reviewed enum-like strings (constants defined at
  call sites; tests assert the observed value sets stay closed).
- `session_id_hash` / `sqlite_source_id_hash`: produced only by
  `observability/privacy.md` helpers; raw `ses_` IDs and raw source-ID strings never appear.
- `request_id`: a validated `req_` ID (via `safe_request_id_from`-equivalent validation) or
  absent — never an arbitrary caller string.
- `correlation_id`: an `err_` ID from `protocol/ids`.

There is deliberately **no free-text message field**. `StructuredLogger` rejects any keyword
argument outside the allowlist: in tests/dev (`__debug__`) it raises `AssertionError`; in
production the field is dropped and a one-time `outcome="log_field_dropped"` warning is emitted.
Values are rendered as one compact JSON object per line on stderr, keys in the fixed allowlist
order, `ensure_ascii=True`.

### Never-logged list (binding; canary-tested)

Event payloads, object plaintext, model cases/output, privacy proposals/approvals, prompts,
agent-context bytes, egress commitments, repository paths, file names, URLs, command lines/output,
environment variable values, provider credential handles/material, privacy authorizations, SQL text or
parameters, actor display names or any actor free text, task titles, exception messages, stack
traces, and raw provider responses. Exceptions are logged only as `correlation_id` +
`outcome="internal_error"`. The exception object is never handed to a formatter; `exc_info`,
`stack_info`, locals, chained exception text, source excerpts, and traceback bytes are discarded
without formatting. v0.1 creates no raw traceback file, owner-only exception log, or hidden debug
sink in any `LogMode`.

### Mode behavior

- `LogMode.service`: one structured stderr `StreamHandler`; no stdout or file handler. The
  root-level all-level third-party filter described below applies to every handler because provider,
  keyring, HTTP, async, and control dependencies execute in this process. Ordinary control bodies,
  provider bodies/responses, YZH1 previews/decisions, YZS1 frames, and secret-memory values are never
  passed as logger messages, args, or fields. Service lifecycle faults emit only the closed
  component/operation/outcome/correlation identity.
- `LogMode.cli`: stderr `StreamHandler` only. stdout is owned by command output (`cli/app.md`).
- `LogMode.mcp_stdio`: stderr `StreamHandler` only, and additionally:
  - a root-level `logging.Filter` is installed on **every** handler that intercepts records from
    non-Yoetz loggers (`mcp`, `mcp.*`, `anyio`, `asyncio`, `pydantic`, `openai`, `httpx`,
    `keyring`, and any logger not under `yoetz.*`) at **all levels including `ERROR` and
    `CRITICAL`** (the transport boundary in `specs/src/yoetz/adapters/mcp_stdio.md`).
    Intercepted records are replaced by a
    fixed-shape sanitized line: `component="sdk"`, `operation="filtered_record"`, `level`,
    `correlation_id` — the original message, args, and exc_info are discarded (a payload,
    tool name, parse detail, or stream exception can never escape);
  - no handler, formatter, or filter ever writes to stdout; `configure_logging` asserts at
    install time that no existing root handler targets `sys.stdout` and removes/repoints any
    that does (e.g. from an imported library calling `basicConfig`);
  - `logging.lastResort` is replaced with the stderr handler.
- `LogMode.confidential_helper`: one structured stderr `StreamHandler`; no stdout/file handler and
  no log sink targets `/dev/tty` (the TTY belongs only to trusted preview/no-echo interaction). The
  all-level third-party filter is installed on every handler and discards original message, args,
  and `exc_info`. YZH1 preview/action/result frames and YZS1 binding/secret frames are never logger
  arguments, even when structurally valid; before/after-ceremony diagnostics are limited to fixed
  operation/outcome/correlation IDs. While a secret view exists, no logging call is made.

The `service`, `mcp_stdio`, and `confidential_helper` modes share the same root/handler replacement
rule for imported libraries: any record not produced through `StructuredLogger` is reduced to the
fixed sanitized shape before formatting. A dependency cannot bypass this by using a child logger,
`ERROR`/`CRITICAL`, `exc_info`, `stack_info`, `logging.lastResort`, or an existing handler.

### Levels and configuration

`logging.level` from `LoggingConfig` (`config/load.md` precedence, so
`YOETZ_LOG_LEVEL`/`--log-level` override the file). Yoetz loggers map 1:1 onto stdlib
levels; `debug` never unlocks payload logging — the allowlist is level-independent.

### Failure containment

Both `record_*_without_raising` helpers are wrapped so that any internal failure (disk full,
encoding error, closed stderr) is swallowed after best-effort emission; they must be safe to
call from the MCP last-resort catch path. The fault-injection matrix in
`specs/tests/conformance.md` makes the safe helpers throw and verifies that exception text still
never reaches MCP content; `specs/tests/subprocess/test_mcp_stdout_purity.py.md` independently
proves protocol-only stdout.

The helpers inspect neither `exc.args` nor traceback state. Their only exception-dependent output
is a `reason` token looked up from one closed reviewed registry keyed by exact exception class
name; an unlisted class (including any dynamically created one, whose name could carry caller
data) resolves to the fixed `exception_unavailable` sentinel, so no derived class name is ever
rendered. They
also emit a newly generated correlation ID plus bounded component/operation/outcome identity. If
even that structural emission fails, they return/exit according to the public boundary without
creating a secondary diagnostic artifact.

## Errors and edge cases

- Logging never raises into application code paths (`logging.raiseExceptions = False`).
- Closed/broken stderr (client killed): writes fail silently; process behavior is governed by
  the transport/CLI layer, not logging.
- Double `configure_logging` calls are idempotent. A later call replaces the same single structural
  stderr sink and reconfigures level/mode/clock without accumulating handlers; this also removes a
  handler that an imported library installed between calls.
- A YZH1/YZS1 preview, binding, frame, or secret mistakenly offered as a message/arg is discarded
  before formatting and increments only the in-memory fixed `log_field_dropped` counter; it is never
  rendered to any sink.
- Records emitted before `configure_logging` (import-time) must not exist: `__init__.md` bans
  import side effects; tests import every module and assert zero records.
- Every process entry point installs the sink for its own mode before doing any other work:
  `service/daemon.md` `run_service` uses `LogMode.service` and `mcp/server.md` `main` uses
  `LogMode.mcp_stdio`. A process that never calls `configure_logging` silently discards every
  record, because `StructuredLogger` carries its content in `extra` and only this module's handler
  renders it; tests assert each entry point installs its mode.

## Invariants

- stdout is never written by any logging path in any mode.
- Only the thirteen allowlisted fields ever appear in a stderr log line.
- SDK/third-party records are sanitized at all levels; filtering is structural, not
  level-based.
- No user-controlled plaintext reaches logs; `specs/src/yoetz/observability/privacy.md` and
  `specs/scripts/scan_public_boundary.py.md` own the corresponding canary gates.
- Log output ordering/content never feeds back into deterministic behavior.
- No logging API accepts YZH1 preview/decision content, YZS1 frame/secret content, or a generic
  positional message/args payload in any mode.

## Tests

- `specs/tests/unit.md` — `tests/unit/observability/test_logging_allowlist.py`: field allowlist
  enforcement, unknown-field drop/assert, timestamp format, filter replaces SDK records at
  ERROR/CRITICAL, never-raise wrappers under injected faults, hostile exception objects are never
  stringified, and traceback formatting/capture hooks are never invoked.
- `specs/tests/subprocess.md` — stdout-purity: `mcp serve` under load with a chatty fake SDK
  logger emits protocol-only stdout; stderr lines all parse as allowlisted JSON.
- `specs/tests/subprocess.md` — service and confidential-helper modes inject hostile dependency
  logs plus YZH1 preview/YZS1 secret canaries through message/args/`exc_info`; only fixed structural
  stderr identity survives and no stdout/file/TTY log sink exists.
- `specs/tests/conformance.md` — plaintext-canary sweep includes stderr capture and the
  complete service/client data, cache, log, temp, export, and diagnostic roots and proves no raw
  traceback artifact is created.

## Open questions

None.

Raw traceback capture is absent from v0.1 in every profile, including release probes. A future
encrypted diagnostic capture would require a separate reviewed content schema, encrypted artifact
path, explicit local privacy authorization, minimization/never-send policy, size/retention rules,
and release evidence. It cannot be added as a `LogMode`, debug flag, ordinary support bundle, or
plaintext owner-only file, and public errors can never depend on it.

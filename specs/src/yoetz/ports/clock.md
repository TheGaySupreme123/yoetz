# src/yoetz/ports/clock.py — injectable wall-clock and monotonic-deadline boundary

**Wave:** B | **ADRs:** ADR-002 (timestamp spelling), ADR-001 (lease expiry is subordinate to
generation fencing) | **Imports (spec-tree):** `protocol/errors.md` | **Imported by:**
`application/service.md`, `application/start.md`, `application/publish_work.md`,
`application/check.md`, `application/respond.md`, `application/receipt.md`,
`adapters/sqlite/connection.md`, `adapters/sqlite/repository.md`,
`adapters/sqlite/start_catalog.md`

## Purpose

All observable wall time enters Yoetz through one injected boundary. The clock supplies metadata
(`occurred_at`, `accepted_at`, expiry, duration anchors), but never ledger order, IDs, causal
relationships, deterministic findings, or ranking. Centralizing it makes crash/lease tests
repeatable and prevents reducers from reading ambient time.

## Public surface

- `class ClockPort(Protocol)` with `def now_utc(self) -> datetime` and
  `def monotonic_seconds(self) -> float` (INTERFACES §10).

Pure `format_rfc3339_millis`, `parse_rfc3339_millis`, and checked UTC duration arithmetic belong to
`domain/values.py`. Production and test clock implementations live at the composition/test
boundary; this module contains no mutable global clock.

## Behavior

### `now_utc`

1. Return a timezone-aware `datetime` denoting UTC. A non-UTC offset is not returned by a conforming
   implementation; callers still normalize defensively through `format_rfc3339_millis`.
2. Each application operation captures `now = clock.now_utc()` once per logical decision or
   transaction input. It does not call the clock repeatedly while comparing one lease.
3. Production uses the operating-system wall clock. Tests supply a scripted clock whose values
   advance only when the test says so; no test sleeps to make a lease expire.
4. A clock value is metadata. SQLite counters assign ingestion order, explicit event IDs/parents
   carry identity/causality, and policy output depends only on recorded canonical values.

### Canonical timestamp helpers (owned by `domain/values.py`)

1. Require a timezone-aware `datetime`; a naive value raises
   `ProtocolValueError("timestamp_timezone_missing")`.
2. Convert the instant to UTC, truncate (never round) microseconds to whole milliseconds, and
   render `YYYY-MM-DDTHH:MM:SS.mmmZ` with exactly three fractional digits and an uppercase `Z`.
3. Reject years outside `0001..9999` and any value the standard calendar cannot represent with
   `ProtocolValueError("timestamp_out_of_range")`.
4. Rendering is locale independent. It never accepts or emits a leap-second spelling; parsing and
   validation of caller strings remain in `domain/values.py`.

### Lease and deadline use

- Persisted lease expiry is `add_duration(captured_now, configured_duration)`, formatted once.
  `milliseconds` must be a positive bounded integer; otherwise
  `ProtocolValueError("invalid_duration")`.
- A lease is valid only when its owner generation is current **and** its stored expiry is after the
  captured `now`. An expired lease may be reclaimed; a stale generation is invalid immediately,
  even if its wall-clock expiry is in the future.
- Wall-clock reversal may conservatively delay expiry but can never restore a stale generation or
  alter accepted ordering. Large forward jumps may expire work early; recovery resumes from the
  durable phase rather than assuming an external side effect failed.
- Provider call timeouts and in-process cancellation budgets SHOULD use the async runtime's
  monotonic reading. `Deadline` stores a monotonic deadline plus diagnostic UTC expiry and computes
  remaining time against `clock.monotonic_seconds()`. A test advances this source explicitly; no
  implementation consults `time.monotonic()` behind the injected boundary.

## Errors and edge cases

- A naive, unrepresentable, or non-`datetime` value is an internal clock-adapter defect at runtime;
  request-boundary timestamp errors are `INVALID_REQUEST` before the application sees them.
- A production clock failure is sanitized as `INTERNAL_ERROR`; no raw platform exception or local
  timezone name is exposed.
- Duplicate or decreasing timestamps are legal metadata and do not reject an otherwise valid
  event. They never change ingestion order.
- Daylight-saving and local timezone configuration are irrelevant because values are converted to
  UTC before persistence.

## Invariants

1. No domain reducer, deterministic policy, ranker, or receipt conclusion reads ambient time.
2. Every persisted protocol timestamp has exactly the ADR-002 millisecond UTC spelling.
3. No ordering, identity, or causal decision depends only on a timestamp.
4. Generation fencing outranks lease wall time everywhere.
5. Tests can reproduce every time-dependent branch without sleeping or patching global modules.
6. Wall-clock adjustment cannot extend or shorten an already-created in-process provider budget;
   monotonic time never becomes persisted ledger order.

## Tests

- `specs/tests/unit.md`: UTC/non-UTC/naive inputs, truncation at microsecond boundaries, year
  bounds, locale/TZ invariance, checked duration arithmetic.
- `specs/tests/property.md`: arbitrary aware datetimes render to exactly one accepted spelling;
  decreasing/duplicate scripted times never change sequence or replay output.
- `specs/tests/conformance.md`: expired, unexpired, stale-generation, and wall-clock-reversal lease
  cases behave identically in memory and SQLite adapters.

## Open questions

None.

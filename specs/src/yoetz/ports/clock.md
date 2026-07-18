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

Pure `format_rfc3339_millis`, `parse_rfc3339_millis`, and
`add_utc_milliseconds` belong to `domain/values.py`. Production and test clock implementations live
at the composition/test boundary; this module contains no mutable global clock and defines no
second duration-addition helper.

## Behavior

### `now_utc`

1. Return an exact built-in `datetime` with a present zero UTC offset and exact millisecond
   precision. A naive, nonzero-offset, subclassed, or sub-millisecond value is not returned by a
   conforming implementation. Callers validate through the `domain/values.py` helpers; they do not
   repair, truncate, or accept an invalid clock sample.
2. Each application operation captures `now = clock.now_utc()` once per logical decision or
   transaction input. It does not call the clock repeatedly while comparing one lease.
3. Production uses the operating-system wall clock. Tests supply a scripted clock whose values
   advance only when the test says so; no test sleeps to make a lease expire.
4. A clock value is metadata. SQLite counters assign ingestion order, explicit event IDs/parents
   carry identity/causality, and policy output depends only on recorded canonical values.

### `monotonic_seconds`

1. Return a finite, nonnegative `float` from one process-local monotonic time domain. Successive
   samples from one clock instance are nondecreasing. The value has no UTC meaning and is never
   serialized or persisted.
2. Production is the sole boundary allowed to read the operating-system monotonic clock. Tests
   inject a scripted implementation; application, domain, kernel, and provider-adapter code do not
   call `time.monotonic()`, an event-loop clock, or another ambient source directly.
3. Callers capture one sample per decision and pass it explicitly to process-local values such as
   `Deadline.remaining_seconds(now_monotonic)` and `Deadline.expired(now_monotonic)`. Those values
   never obtain a current time themselves.
4. A restarted process begins a new monotonic domain. It reconstructs any in-process budget from
   current durable authority and a fresh sample; no `Deadline` crosses the restart boundary.

### Canonical timestamp helpers (owned by `domain/values.py`)

1. Require `type(dt) is datetime`; a subclass or other value raises
   `ProtocolValueError("invalid_timestamp")`. A naive value raises
   `timestamp_timezone_missing`, a nonzero offset raises `timestamp_not_utc`, and a microsecond not
   divisible by 1000 raises `timestamp_submillisecond_precision`, in that order.
2. Normalize an accepted zero-offset instant to `timezone.utc` and render
   `YYYY-MM-DDTHH:MM:SS.mmmZ` with exactly three fractional digits and an uppercase `Z`. The helper
   never rounds or truncates.
3. `add_utc_milliseconds` applies the same datetime validation, then requires an exact built-in
   `int` in `1..9_007_199_254_740_991`; every other duration raises `invalid_duration`.
   `timedelta`/calendar overflow raises `timestamp_out_of_range` and is never clipped.
4. Rendering and arithmetic are locale independent. Neither accepts or emits a leap-second
   spelling; parsing and validation of caller strings remain in `domain/values.py`. Normalization
   before arithmetic makes an original named timezone's later offset transition irrelevant.

### Lease and deadline use

- Persisted lease expiry is
  `add_utc_milliseconds(captured_now, configured_duration_milliseconds)`, formatted once. There is
  no `add_duration` alias or second duration domain.
- A lease is valid only when its owner generation is current **and** its stored expiry is after the
  captured `now`. An expired lease may be reclaimed; a stale generation is invalid immediately,
  even if its wall-clock expiry is in the future.
- Wall-clock reversal may conservatively delay expiry but can never restore a stale generation or
  alter accepted ordering. Large forward jumps may expire work early; recovery resumes from the
  durable phase rather than assuming an external side effect failed.
- Provider call timeouts and in-process cancellation budgets MUST use the injected
  `ClockPort.monotonic_seconds()` domain. `ports/semantic.py` owns the frozen
  `Deadline(expires_at_utc, monotonic_deadline)` value. Its exact methods are
  `remaining_seconds(now_monotonic: float, /) -> float` and
  `expired(now_monotonic: float, /) -> bool`; the caller supplies the captured sample. A test
  advances this source explicitly; no deadline method or adapter consults ambient time.
- The coordinator creates a deadline by capturing wall and monotonic values separately and adding
  the same configured duration within each domain. `expires_at_utc` is diagnostic only. It is not
  subtracted from wall time to enforce the budget, and no wall-clock change alters an existing
  monotonic deadline.

## Errors and edge cases

- A naive, nonzero-offset, sub-millisecond, unrepresentable, subclassed, or non-`datetime` value is
  an internal clock-adapter defect at runtime; request-boundary timestamp errors are
  `INVALID_REQUEST` before the application sees them.
- A production clock failure is sanitized as `INTERNAL_ERROR`; no raw platform exception or local
  timezone name is exposed.
- Duplicate or decreasing timestamps are legal metadata and do not reject an otherwise valid
  event. They never change ingestion order.
- A nonfinite, negative, non-`float`, or decreasing monotonic sample is an internal clock-adapter
  defect. It is never coerced into a duration or exposed as a public provider failure.
- Daylight-saving and local timezone configuration are irrelevant because only zero-offset samples
  are accepted and each accepted instant is normalized to `timezone.utc` before arithmetic and
  persistence.

## Invariants

1. No domain reducer, deterministic policy, ranker, or receipt conclusion reads ambient time.
2. Every persisted protocol timestamp has exactly the ADR-002 millisecond UTC spelling.
3. No ordering, identity, or causal decision depends only on a timestamp.
4. Generation fencing outranks lease wall time everywhere.
5. Tests can reproduce every time-dependent branch without sleeping or patching global modules.
6. Wall-clock adjustment cannot extend or shorten an already-created in-process provider budget;
   monotonic time never becomes persisted ledger order.
7. Deadline evaluation is a pure function of its frozen monotonic deadline and the explicit current
   monotonic sample; diagnostic UTC expiry cannot affect it.

## Tests

- `specs/tests/unit.md`: exact UTC/non-UTC/naive/subclass inputs, rejection (not truncation) at
  sub-millisecond boundaries, year bounds, locale/TZ invariance, checked duration bounds and
  overflow, zero-offset named-zone transition arithmetic, explicit monotonic deadline
  before/equal/after boundaries, and invalid monotonic samples.
- `specs/tests/property.md`: arbitrary exact-millisecond zero-offset datetimes render to exactly one
  accepted spelling, while other aware offsets reject; decreasing/duplicate scripted times never
  change sequence or replay output.
- `specs/tests/conformance.md`: expired, unexpired, stale-generation, and wall-clock-reversal lease
  cases behave identically in memory and SQLite adapters.

## Open questions

None.

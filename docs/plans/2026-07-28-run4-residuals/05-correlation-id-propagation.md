# 05 — The correlation id an agent is shown must resolve

**Severity:** medium **PR boundary:** `ControlError` correlation identity + bridge reuse

## The defect

Every failure the agent sees carries a `correlation_id`. For everything except an accepted
`publish_work`, that id resolves to nothing, because the id written to the durable diagnostic sink
and the id handed to the caller are different ids.

The previous residual set built the durable sink specifically so "the id in an agent-facing error
resolves to something." It half-works: the records are there and they are excellent, but the id
printed to the agent is not the id under which they are filed.

## Evidence

Run 4, check #1. What the agent was shown, and what the sink holds at the same instant:

```
$ yoetz service diagnostics --correlation-id err_c233d86e-3e7e-441a-a4e7-b6379f096e8f
{"correlation_id":"err_c233d86e-…","count":0,"records":[]}

$ yoetz service diagnostics --correlation-id err_7b8ec46e-8d0b-42cf-ba12-85ca1317fc6d
{"count":1,"records":[{"component":"service.daemon",
  "operation":"check_response_projection_failed","reason":"exception_validation_error",
  "request_id":"req_a8b4c004-f0d4-4fbd-825e-0d72e37cae06",
  "timestamp":"2026-07-28T09:56:10.989Z"}]}
```

Same failure, same millisecond, two ids. The agent-facing one is a dead end.

The cause is structural. `ControlError` has no correlation field at all —
`src/yoetz/ports/control.py:270`:

```python
__slots__ = ("accepted_state", "reason", "retryable")
```

The daemon mints one and uses it only for the reduced publish envelope
(`src/yoetz/service/daemon.py:926-942`):

```python
correlation_id = record_unexpected_exception_without_raising(...)
if request.method is ControlMethod.PUBLISH_WORK:
    reduced = _publish_accepted_projection_unavailable(internal, correlation_id=correlation_id, ...)
    ...
raise ControlError(reason, retryable=True, accepted_state=accepted_state) from exc
```

The id dies with that statement. The bridge then mints a fresh one for the client
(`src/yoetz/mcp/server.py:283`):

```python
correlation_id if correlation_id is not None else new_id(IdKind.CORRELATION)
```

The comment at `daemon.py:924-925` states the intent — *"One correlation id for the unexpected
failure path: shared by the reduced publish envelope and the diagnostic ring; no second mint on
ControlError fallback"* — and it is true only for `publish_work`.

Exactly one run-4 correlation id resolved: `err_7e5c31e9-…`, the `status view=operation` failure,
because it was minted at the bridge where the client id is also minted.

## Design

### 1. Carry the correlation id on `ControlError`

Add an optional `correlation_id` to `ControlError` and populate it wherever the daemon has already
minted one. It is a bounded, structural, service-generated identifier with no caller content — it
belongs in the same category as `reason` and carries no disclosure risk.

Keep the existing `bind_correlation_id` behaviour at the bridge as the fallback for errors that
genuinely originate there, so a bridge-level failure still gets an id.

### 2. Bridge reuses rather than mints

Where a `ControlError` arrives carrying an id, the bridge must surface that id to the client rather
than generating a new one. Where it does not, the bridge mints and records under its own id, as
today.

### 3. Never two ids for one failure

The invariant to pin: for any single failure, the id in the agent-facing error and the id in the
diagnostic sink are the same string. A test must assert this for a read failure, a write failure,
and a bridge-level failure — not just for the accepted-publish path that already works.

## Files

- `src/yoetz/ports/control.py` — `ControlError` correlation field
- `src/yoetz/service/daemon.py` — pass the minted id into the raised error
- `src/yoetz/mcp/server.py` — reuse rather than mint
- `src/yoetz/cli/` — no change expected; `yoetz service diagnostics` already reads the sink

## Tests

- Force a `check` response-projection failure: the `correlation_id` in the public error resolves
  through `yoetz service diagnostics --correlation-id` to exactly one record.
- The same for a `status` read failure (`read_projection_failed`).
- A bridge-level failure with no service-side id still produces a resolvable id.
- An accepted `publish_work` reduced envelope keeps today's behaviour — the envelope id and the
  sink id already match, and must continue to.
- The correlation id never appears in any durable record alongside exception text, payload, or a
  filesystem path.

## Done

Green CI, and every agent-facing correlation id resolves to its record.

## Dogfood observable

Run 5: any correlation id appearing in an agent-facing error resolves through
`yoetz service diagnostics --correlation-id` to exactly one record naming the failing operation.

## Out of scope

What the diagnostic records contain, and the sink's format or retention. Adding new diagnostic
emission points — plan 02 owns the semantic ones.

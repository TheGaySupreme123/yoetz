# 01 — Recovery must not mask a known authoring error

**Severity:** critical  
**PR boundary:** publish envelope recovery result selection in the MCP bridge

## The defect

A malformed `decision_recorded.authority` should return `INVALID_REQUEST` at
`/event_drafts/0/payload/authority`. Instead, the dogfood returned:

```text
INTERNAL_ERROR
reason_code: read_projection_failed
No durable state changed. Repeat with a new request_id.
```

That result did not come from the `publish_work` handler. `PublishWorkRequest.model_validate`
rejected the payload first. `dispatch_publish_work` then called
`_publish_recovery_from_envelope` so a malformed replay body could still recover a prior committed
operation. The nested `status view=operation` request raised the run-4 `AttributeError`; the bridge
returned that read failure as the outer publish result and discarded the original validation
error.

The durable ring proves the path. The four published correlation IDs are recorded as
`service.daemon / status_read_projection_failed / exception_attribute_error`, each with a
service-generated status request ID different from the public publish request ID.

Current `main` already validates the literal payload as:

```text
field: /event_drafts/0/payload/authority
reason: invalid_type_or_value
```

and the focused operation-view suite passes. The remaining defect is result precedence when the
secondary recovery read is unavailable.

## Design

### 1. Make recovery lookup a closed tri-state

`_publish_recovery_from_envelope` must distinguish:

- **authoritative found** — pending, complete, or quarantined operation;
- **authoritative absent** — lookup succeeded and no operation exists;
- **lookup unavailable** — connection, timeout, projection, or unexpected recovery failure.

Only “found” replaces the body-validation result. “Absent” returns the original field-pointed
validation result.

### 2. Do not lie when lookup is unavailable

An unavailable lookup cannot prove whether the request ID already names a committed write.
Therefore it must not return the nested read message “No durable state changed,” and must not tell
the caller to use a new request ID.

Return a retryable `OPERATION_PENDING`-class result with a fixed reason such as
`operation_recovery_unavailable`, the original request ID, and the safe validation locations that
will apply if the operation is authoritatively absent. The remedy is:

1. retry lookup with the same request ID;
2. if lookup says absent, correct the named field and use the intended request identity;
3. if lookup says complete, recover the stored result.

No nested status request ID or nested read-only durability claim becomes the outer operation
meaning.

### 3. Preserve the PR #47 recovery guarantee

Do not simply skip recovery when body validation fails. A malformed retry body may still carry a
request ID for a committed operation. Complete, pending, and quarantined recovery results retain
precedence over authoring diagnostics.

### 4. Keep dry-run honest

`dry_run: true` creates no operation record. A fresh dry-run with a malformed field should normally
reach the authoritative-absent branch and return the validation pointer. Do not special-case it in
a way that allows a reused request ID to bypass operation identity checks.

## Files

- `src/yoetz/mcp/server.py` — recovery result type and precedence
- `src/yoetz/protocol/errors.py` / public schemas — only if a new reason token is required
- `docs/INTERFACES.md` — recovery-unavailable meaning and same-ID remedy
- tests under `tests/unit/mcp/` and `tests/integration/application/`

## Tests

- The literal dogfood `authority` value with an absent operation returns `INVALID_REQUEST` and
  `/event_drafts/0/payload/authority`.
- A completed operation wins even when the supplied retry body is invalid.
- Pending and quarantined operation states retain their current bounded meanings.
- A recovery read raising `read_projection_failed` returns
  `operation_recovery_unavailable`, not the nested read message.
- The unavailable result uses the original publish request ID and tells the caller to retain it.
- No unavailable branch claims that durable state did or did not change.
- A malformed dry-run does not append, consume the request ID, or move the frontier.
- Hostile payload text cannot appear in the error or diagnostics.

## Done

A failed recovery oracle can delay an answer but cannot replace a known authoring error with a
false durability statement.

## Dogfood observable

The malformed-authority probe returns the exact safe field pointer on an exact newly packaged
runtime. An injected operation-read failure returns an ambiguity-safe same-ID remedy.

## Out of scope

Fixing `status view=operation` itself; PRs #62 and #63 own that. Changing the
`decision_recorded.authority` type.


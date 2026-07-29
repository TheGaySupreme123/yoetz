# 04 — Semantic attempts must be durable and obey the configured budget

**Severity:** high  
**PR boundary:** semantic operation orchestration, attempt persistence, retry/deadline policy

**Depends on:** [02](02-one-provider-judgment-contract.md) and
[03](03-send-the-real-frozen-review-packet.md).

## The defect

The dogfood required eight agent-issued checks to obtain three valid semantic responses. Four
responses were invalid and one provider response was labeled timeout.

ADR-006 specifies one durable semantic operation with at most two retries, a total deadline, and
fresh authorization/receipt/transport/credential identity for each physical dispatch. Production
currently performs one attempt:

- SDK retry is correctly disabled;
- no Yoetz retry coordinator consumes `ProviderProfileConfig.max_retries`;
- `timeout_seconds` is copied into a profile but the check coordinator hard-codes 60 seconds;
- `semantic_jobs` and `semantic_attempts` tables/ports have no production caller;
- invalid/timeout attempt facts disappear after the check response;
- each manual retry appends another `check_recorded` event.

The configuration advertises behavior that does not exist.

## Design

### 1. One durable semantic job per check

Create or recover the semantic job after deterministic freeze/pinning and before external
dispatch. Bind it to operation ID, case digest, dependency digest, provider binding, policy
generation, and total deadline.

Crash/replay loads this job. No memory-only object is recovery authority.

### 2. One durable row per physical attempt

Before each dispatch, reserve the next bounded attempt ordinal and identity. Complete it with:

- selected/final flag;
- semantic status/reason/failure class;
- provider request ID when returned;
- latency and bounded usage/cost facts;
- authorization/reservation/receipt/commitment identities after terminal receipt closure;
- bounded raw size/truncation/parse-stage facts where applicable.

Store no raw provider text, prompt, secret, path, or user-controlled diagnostic prose.

### 3. Honor total timeout and retry count

Use configured `timeout_seconds` as the total semantic-operation deadline and `max_retries` as the
maximum additional physical attempts, capped by ADR-006.

Retry only the approved transient classes: timeout/connection/429 equivalents and contract-invalid
output only if the plan explicitly admits one bounded repair retry. Never retry policy block,
human denial, secret/never-send detection, invalid case refs, stale frontier, or exhausted
authority.

Each physical attempt uses a new dispatch ID, authorization, receipt, SDK client, transport, and
credential handle. `confirm_every_request` requires a new foreground human decision per retry;
without it the job completes incomplete rather than silently reusing consent.

### 4. Select once, append once

Choose the first valid, current judgment as selected. Late or non-selected attempts remain audit
rows and cannot replace it. Exhaustion returns one final exact status/reason and appends one
`check_recorded` event preserving deterministic truth.

### 5. Make accounting readable

Expose bounded structural attempt accounting through check result and operation recovery:
attempted count, selected attempt ID, terminal status/reason counts, and exhausted/not-exhausted.
Owner diagnostics may mirror bounded reason tokens. Do not add telemetry or a network channel.

## Files

- `src/yoetz/application/check.py` / semantic coordinator
- `src/yoetz/application/egress.py`
- semantic job/attempt ledger ports and SQLite/memory adapters
- provider profile composition
- check/status result models, schemas, fixtures, and `docs/INTERFACES.md`
- conformance, crash/recovery, privacy, and integration tests

## Tests

- Zero-retry configuration performs exactly one attempt.
- Two-retry configuration performs at most three physical attempts inside one total deadline.
- Every attempt has unique dispatch, authorization, receipt, credential-handle, and provider
  identity.
- `confirm_every_request` cannot reuse approval for a retry.
- Crash before authorization consumption resumes the same attempt; crash after consumption never
  reuses it.
- Invalid, timeout, 429, refusal, policy block, stale, and success paths follow the admitted retry
  matrix.
- Exactly one final check event is appended; deterministic findings survive exhaustion.
- Status/operation recovery reconstructs attempt accounting from durable rows.
- No durable row or diagnostic contains provider plaintext or user-controlled content.

## Done

Configuration, ADR-006, durable state, and observed physical dispatch count agree.

## Dogfood observable

One check either succeeds within its configured attempt budget or returns one incomplete result
whose durable attempt table explains every bounded attempt and terminal reason.

## Out of scope

General product telemetry, raw-response capture, or retrying with a different model/provider.


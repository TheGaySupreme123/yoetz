# 07 — Distinguish caller-claimed time from service acceptance time

**Severity:** medium  
**PR boundary:** event history projection and temporal guidance

## The defect

The first dogfood batch claimed `occurred_at=2026-07-28T09:00:00.000Z` and was accepted about
9.5 hours later. Later events also used synthetic whole-second timestamps.

This is **not ledger corruption**:

- `occurred_at` is caller supplied;
- the service independently stamps `ledger.accepted_at`;
- both values are durable and included in the entry-digest preimage;
- ordering, causality, supersession, and optimistic concurrency use ingestion sequence/frontier,
  not caller time.

The honesty defect is the read projection. `status view=history` exposes `occurred_at` but omits
`accepted_at`, so a reader sees a caller claim presented like a service timestamp. The write
response is the only ordinary place acceptance time appears, and an auditor without the original
request ID cannot recover it.

The MCP descriptor also shows a hard-coded `occurred_at` example and does not explain its meaning.

## Design

### 1. Keep caller time as a claim

Do not add arbitrary skew, monotonicity, or “must precede acceptance” rejection. Agents may report
work performed offline or before attachment, and the service has no authority to verify an outside
clock. A future stronger observation profile may engine-stamp its own events, but that does not
make cooperative caller time verified.

### 2. Expose both clocks in history

Add digest-bound `accepted_at` to each `StatusHistoryItemModel` and its public schema. Render the
pair with unambiguous names:

- `occurred_at` — caller-asserted event time;
- `accepted_at` — trusted-local service acceptance time.

History ordering remains ingestion sequence. Do not sort or filter by `occurred_at`.

### 3. Explain authoring behavior

The event-draft schema, MCP descriptor, and publication guidance state:

- use the best real RFC3339-millisecond UTC time available;
- do not copy the worked-example timestamp;
- if the exact event time is unknown, use an honest bounded approximation and understand that it is
  a claim;
- ledger order and receipt freshness come from frontier/acceptance, not caller time.

Generate examples dynamically only if that can stay deterministic and schema-owned; otherwise mark
the checked-in timestamp explicitly as illustrative.

### 4. Keep temporal honesty per event

Do not add a new aggregate coverage dimension in this plan. Mixed engine-stamped and caller-asserted
events make a single task-level token lossy. The visible per-event pair is the source of truth.
Receipts remain frontier-bound and make no claim that caller timestamps were verified.

## Files

- `src/yoetz/protocol/models.py`
- status application/repository projection types
- status schemas, fixtures, and generated/frozen pointer inventories
- MCP descriptors and publication/coverage guidance
- `docs/INTERFACES.md` and the owning canonical-protocol ADR if required
- history projection and privacy tests

## Tests

- Far-past, future, and out-of-order caller timestamps remain accepted as claims.
- Each history item exposes the exact stored `occurred_at` and `accepted_at`.
- Entry digest verification still binds both values.
- History order remains `ingestion_sequence`.
- Privacy projection treats `accepted_at` as structural metadata and does not disclose new content.
- Receipt wording does not upgrade caller time to verified time.
- Guidance/descriptors distinguish the two clocks and do not instruct agents to copy a placeholder.

## Done

Any reader who can see an event's claimed time can also see when the trusted local service accepted
that claim.

## Dogfood observable

Publish one intentionally backdated event and read history. The response shows both timestamps,
retains ingestion order, and uses no warning that implies Yoetz verified the outside event time.

## Out of scope

Trusted external timestamping, NTP attestation, clock-skew policy, temporal coverage lattices, or
changing receipt frontier semantics.


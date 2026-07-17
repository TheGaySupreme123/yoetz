# src/yoetz/kernel/projections.py — immutable projection state and projection storage shape

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`domain/events.md`, `domain/findings.md`, `domain/receipts.md`, `protocol/coverage.md`,
`protocol/canonical.md` | **Imported by:** `ports/ledger.md`,
`kernel/reducers.md`, `kernel/ranking.md`, `kernel/receipt_builder.md`,
`adapters/sqlite/migrations.md`, `adapters/sqlite/repository.md`

## Purpose

This file defines the pure derived work state that Yoetz rebuilds from the ledger. The
projection is the system’s current understanding of plans, obligations, actions, results,
evidence, claims, contradictions, findings, responses, freshness, and unknown-event gaps. It is
the structure that reducers write, rankers read, receipt builders summarize, and SQLite persists in
typed projection tables.

The projection is not the ledger. It is a deterministic cache of meaning built from accepted
events. Its job is to be replayable, small enough to inspect, and strict enough that a corrupted or
stale projection can be discarded and rebuilt without changing the underlying event history.
It is a pure upstream value module: it never imports `ports/ledger.py` or `version.py`.
`ports/ledger.py` may name `ProjectionState` in boundary records, not the reverse.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `PROJECTION_VERSION` | `str = "yoetz/0.1.0"` |
| `PROJECTION_GENERATION` | `int = 1` |
| `ProjectionState` | frozen dataclass containing the derived work snapshot |
| `empty_projection_state()` | construct the empty derived state for a new bundle or replay |
| `projection_snapshot(state)` | canonical JSON-compatible view used for digesting and persistence |
| `projection_digest(state)` | `sha256:` digest of the canonical projection snapshot |

## Behavior

`ProjectionState` is a frozen dataclass with the exact top-level fields from the shared registry:
`frontier`, `head_digest`, `plans`, `obligations`, `decisions`, `assignments`, `actions`,
`results`, `evidence`, `claims`, `contradictions`, `findings`, `responses`,
`latest_tested_state`, `freshness`, `unknown_event_count`, and `coverage_gaps`.

`latest_tested_state`, when present, is the frozen latest-check record containing the source check
event ID, its exact subject frontier, `CheckVerdict`, exact `returned_finding_ids`, nonnegative
`suppressed_count`, and the `Coverage` carried by `CheckRecordedPayload`. Projection freshness and
normalized gaps may weaken from that coverage but may never reconstruct a stronger value or infer
suppressed identities from the returned finding IDs.

Those collections are immutable mappings or tuples of current visible records, not live database
rows. The mapping keys are stable logical IDs:

- `plans` keyed by plan version;
- `obligations` keyed by `obligation_id`;
- `decisions` keyed by the decision event ID;
- `assignments` keyed by the assignment event ID;
- `actions` keyed by `action_id`;
- `results` keyed by `result_id`;
- `evidence` keyed by `evidence_id`;
- `claims` keyed by `claim_id`;
- `contradictions` keyed by a stable derived contradiction key;
- `findings` keyed by `finding_id`;
- `responses` keyed by `finding_id`.

The stored record values are frozen, implementation-local records that retain the current visible
body, the source event metadata, and the minimum derivation data needed to explain how the state
was produced. The projection never stores raw event payload bytes; it stores the canonical record
shapes that reducers derived from them.

Each stored record is the current visible projection for its logical subject. The record keeps the
stable logical key, the source event ID, the source frontier at which it became visible, the body
needed by rankers and receipt builders, and the coverage/freshness note that explains why it is
present. The projection does not store ambient object-store bytes, provider transcripts, or any
open-ended JSON blob that would require a second interpretation step.

This module owns the pure typed projection shape and derivation semantics, not executable SQL bytes.
The canonical root migration `migrations/bundle/0001.sql` alone owns the exact generation-1 table,
column, constraint, index, statement-order, and newline bytes; its installed resource is an opaque
byte-identical copy. This module exports no DDL string, and migration/runtime code must not append,
template, or synthesize projection SQL from Python. Schema-identity tests instead prove that the
root migration's `p1_` tables can persist and reconstruct these exact frozen record families.

The required generation-1 typed storage families are fixed for v0.1:

- `p1_projection_state` for frontier, head digest, latest-check event/frontier/verdict,
  returned-finding IDs, suppressed count, coverage, freshness, unknown-event count, and rebuild
  metadata;
- `p1_plans`, `p1_obligations`, `p1_decisions`, `p1_assignments`, `p1_actions`, `p1_results`,
  `p1_evidence`, `p1_claims`, `p1_contradictions`, `p1_findings`, and `p1_responses` for the
  derived records;
- `p1_coverage_gaps` for normalized gap markers;
- only the helper edge tables explicitly frozen in the root migration for source references,
  supersession, or subject links.

Every durable row stores the stable logical key plus the minimum fields needed to reconstruct the
frozen projection record. The adapter may store canonical JSON bytes for a body column, but it may
not require a second ad hoc serializer to interpret those bytes.

`empty_projection_state()` returns a state with:

- `frontier` at sequence `0` and head digest `"genesis"`;
- empty mappings for every derived collection;
- `latest_tested_state = None`;
- `freshness = unknown`;
- `unknown_event_count = 0`;
- `coverage_gaps = ()`.

`projection_snapshot(state)` converts the frozen state into a canonical JSON-compatible object with
stable key ordering and stable record ordering. The snapshot is the structure used for digesting
and for the SQLite `state_digest` column. It is not a human render. The top-level object follows
registry order, and each mapping is sorted by the logical key that names the record.

`projection_digest(state)` is the `sha256:` digest of the canonical snapshot bytes. It must be
stable across hash seeds, locales, and installation order.

## Errors and edge cases

- A non-frozen or malformed state record is invalid at construction time.
- A record with duplicate logical keys is a projection bug and must not be silently merged.
- Unknown freshness values or unsupported record shapes are internal errors, not user-facing
  protocol errors.
- The projection never invents missing evidence, claims, or findings to fill gaps.
- A corrupted snapshot digest means the typed projection tables must be rebuilt from the ledger.

## Invariants

1. The projection is derived only from accepted ledger records.
2. Projection snapshots are deterministic and replayable.
3. Unknown events and redactions weaken coverage; they never strengthen the projection.
4. The projection does not read or write SQLite, clocks, providers, or network resources.
5. Generation 1 is the only supported durable projection shape in v0.1.

## Tests

- `specs/tests/conformance.md` — memory and SQLite projection parity over the same event stream.
- `specs/tests/integration.md` — projection corruption, rebuild, and stale-generation handling.
- `specs/tests/unit.md` — snapshot ordering and digest stability.
- `fixtures/projections/` — golden projection snapshots for core event streams.

## Open questions

None.

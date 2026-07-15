# tests/property/ — generated protocol, replay, and state-machine invariants

**Wave:** B–C | **ADRs:** ADR-001 through ADR-004, ADR-006, ADR-008, ADR-009 |
**Imports (spec-tree):** canonical, domain/privacy, kernel, ports, service-control, egress,
memory/SQLite adapter specs |
**Imported by:** PR nightly and release conformance

## Purpose

Use Hypothesis to search value boundaries and operation interleavings that examples miss. Generated
tests assert an independent reference model and public invariants; they must not copy the SQLite
implementation's state transitions and then congratulate it for agreeing.

## Public surface

```text
tests/property/
  strategies/
    json_values.py
    identifiers.py
    events.py
    operations.py
  test_canonical_properties.py
  test_id_properties.py
  test_coverage_lattice.py
  test_reducer_equivalence.py
  test_ranking_properties.py
  test_receipt_properties.py
  test_privacy_properties.py
  test_egress_policy_properties.py
  test_service_control_frames.py
  test_ledger_state_machine_memory.py
  test_ledger_state_machine_sqlite.py
```

The state machine has a pure model and a pluggable system-under-test factory. The same rules run
against memory and SQLite with separate profiles; SQLite examples are fewer/slower but semantically
identical.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
tests/property/strategies/events.py
tests/property/strategies/identifiers.py
tests/property/strategies/json_values.py
tests/property/strategies/operations.py
tests/property/test_canonical_properties.py
tests/property/test_coverage_lattice.py
tests/property/test_egress_policy_properties.py
tests/property/test_id_properties.py
tests/property/test_ledger_state_machine_memory.py
tests/property/test_ledger_state_machine_sqlite.py
tests/property/test_privacy_properties.py
tests/property/test_ranking_properties.py
tests/property/test_receipt_properties.py
tests/property/test_reducer_equivalence.py
tests/property/test_service_control_frames.py
```

## Behavior

### Strategies

Generate the restricted JSON profile recursively with explicit depth/byte budgets: null/booleans,
safe integers, Unicode scalar strings (including Hebrew/emoji/combining marks and normalization
variants), arrays, and unique-key objects. Separate invalid-byte strategies generate duplicate-key
JSON text, invalid UTF-8, BOM/NUL, floats/-0, unsafe integers, lone-surrogate escapes, malformed
framing, unsorted/duplicate set fields.

Generate typed IDs from valid constructors plus single-defect mutations. Event strategies start
from valid family payloads, then mutate exactly one rule for rejection tests. Causal parents,
writer sequence, prior digests, and subject-state refs are model-aware rather than filtered blindly.

### Algebraic properties

- `parse(encode(v)) == v` and re-encoding is byte-identical.
- Object insertion order does not affect bytes; array order does.
- Canonical digest equals independent SHA-256 over emitted fixture bytes.
- Equivalent canonical request values have one request digest; any logical mutation conflicts under
  reused idempotency identity.
- Coverage weakest combine is idempotent/commutative/associative for compatible dimensions; result
  never strengthens an ordered dimension and gaps are sorted unique union.
- Full replay equals any partitioned incremental replay.
- Ranking is permutation-invariant, stable, capped, and deduplicated.
- Receipt conclusion coverage is no stronger than every material dependency.
- Local-control frame encode/decode is a canonical bounded round trip; malformed lengths/JSON,
  unknown methods, and arbitrary chunking never allocate above the cap or create a secret-bearing
  request branch.
- Privacy-policy intersection never widens any category, purpose, scope, destination, channel, or
  ceiling; adding restrictions cannot make a denied case dispatchable. Never-send data is blocked
  under every generated profile/authorization, and a changed prepared-case digest invalidates
  approval.

### Ledger rule-based state machine

Model state tracks catalog routes/start operations, task/session/writer IDs, owner generation,
events and exact canonical bytes, per-writer chains, global frontier/head, idempotency operations,
projection state, objects/staging, redactions, check jobs/attempts, responses, and receipts.

Rules include:

1. reserve/create/attach/create-or-attach start;
2. advance start phase, complete, crash, expire/reclaim, quarantine;
3. acquire/release/take over owner generation;
4. stage/finalize/drop/orphan object;
5. append one/batched known or unknown events;
6. retry same request, retry canonical-equivalent request, reuse key with changed request;
7. publish from independent writers, wrong/skip/duplicate writer sequence/predecessor;
8. expected-frontier success/conflict;
9. freeze case, start/renew/expire semantic job, accept/refuse/invalidate/late response;
10. change frontier before check commit;
11. respond/waive/expire/redact;
12. build/store receipt;
13. delete/rebuild projection cache;
14. backup-model snapshot and restore-model verification.

After every rule, assert:

- global ingestion sequence/head chain contiguous;
- each writer chain contiguous independently;
- acknowledged operation is durable and replayable;
- failed/rejected batch has no partial logical effect or referenced object;
- same request retry returns the same exact public result;
- different request under same key conflicts;
- only current owner generation writes/checkpoints;
- every structural object reference names a durable finalized object or explicit redaction/missing
  state;
- incremental projection equals full reference replay;
- check/semantic result can steer only at frozen current frontier;
- public outcomes match memory/SQLite and contain no plaintext canary.

Privacy generators range across all four disclosure profiles, all five review-context profiles,
every exact `ReviewSelectionPolicy` field, both current-data-use guard values, and independent sink
category/class ceilings. Their meet may only remove selected recorded material; selection can never
grant disclosure authority. Every emitted packet item is case-bound, in scope, under all caps, and
either policy-eligible or represented by one closed omission reason.

Crash rules model semantic before/after-commit uncertainty rather than attempting OS termination in
this suite; physical kill points belong to subprocess tests.

### Reproducibility

CI records Hypothesis seed, example database artifact, package/engine/policy versions, profile, and
normalized failing operation sequence. A minimized regression becomes a named permanent fixture when
it represents a contract boundary or prior incident.

Profiles:

- `pr`: bounded examples/deadline, no SQLite state machine unless changed area requires it;
- `nightly`: larger Unicode/event sequences, memory + SQLite;
- `release`: deterministic seed set, no deadlines on correctness-critical properties, explicit
  global time/resource cap and supervised workers.

## Errors and edge cases

- Excessive `assume` filtering is a suite defect; construct valid model-aware inputs directly.
- Health-check suppression requires a documented reason and bound.
- Hypothesis deadlines never define product timeout behavior.
- Generated secrets/canaries are synthetic and removed with temp bundles.
- SQLite examples use one isolated bundle/process state and owner-safe temp paths, never shared
  developer data.

## Invariants

1. The reference model imports no SQLite/object/provider implementation.
2. A failing example is reproducible from captured seed/sequence.
3. Generated invalid cases differ in one named rule where practical.
4. State-machine cleanup is bounded and cannot kill unrelated processes.
5. Property success cannot replace golden cross-language vectors or physical crash tests.

## Tests

```bash
uv run --locked pytest tests/property -m "not sqlite" -q --timeout=120
uv run --locked pytest tests/property -m sqlite -q --timeout=300
```

CI also replays committed regression examples before random generation and runs deterministic
controls across hash seed/locale/TZ/page-size variants.

## Open questions

None.

E-005 is the sole central runtime-budget gate.

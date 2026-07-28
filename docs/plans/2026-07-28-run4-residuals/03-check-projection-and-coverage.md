# 03 — A check must not erase the record that it happened

**Severity:** high (honesty) **PR boundary:** the SQLite work-projection update statements

## The defect

Running a check leaves no trace in the durable projection, and actively overwrites the task's
coverage vector with `check_types: ["none"]` — the claim that no check has been performed.

The coverage vector is the load-bearing artifact of the product's honesty promise: *"It will tell
you exactly what was checked, at what coverage."* Today a check makes that vector say less than the
truth, permanently, including after a fully successful semantic verification.

## Evidence

A fresh task on 2026-07-28. Check A at ingestion sequence 7 succeeded with live semantic
verification (`semantic_status: succeeded`, `provider_request_id: resp_357677ac…`,
`coverage.check_types: ["deterministic", "semantic_model_derived"]` in the response). A second
check landed at sequence 13. The durable projection afterwards:

```
frontier_seq              = 13
latest_check_event_id     = None
latest_verdict            = None
latest_coverage_canonical = None
p1_query_checks           = 0 rows
status_coverage_canonical = {… "check_types":["none"] …}
```

Sixteen events, `applied_through_seq = 16`, `unknown_event_count = 0` in run 4's task — the events
were applied; nothing about the check was retained.

Two distinct causes, both in `src/yoetz/adapters/sqlite/repository.py`:

**Cause 1 — no check column is ever written.** Both projection updates (line 973, the ordinary
append path, and line 1091, `_persist_derived_records`, which is the path a check takes) set only:

```
frontier_seq, head_digest, open_obligation_count, unresolved_finding_count,
freshness, unknown_event_count, task_title_source_event_id,
status_coverage_canonical, status_gap_codes_canonical
```

`latest_check_event_id`, `latest_subject_frontier_seq`, `latest_subject_frontier_digest`,
`latest_verdict`, `latest_returned_finding_ids`, `latest_suppressed_count`, and
`latest_coverage_canonical` are never assigned after the seed insert in
`src/yoetz/adapters/sqlite/migrations.py:185`. **The columns already exist**, so no migration and
no schema-version bump is required.

**Cause 2 — coverage is taken from the last event of the batch.** Both updates write:

```python
canonical_encode(coverage_to_json(records[-1].coverage))
```

For an engine-derived check batch the last record is `check_recorded`, whose *envelope* coverage is
`check_types: ["none"]` with `publication_channels: ["engine_derived"]`. So the check's own event
overwrites the task's status coverage with the assertion that nothing was checked.

This is why `status view=versions` reported `check_types: ["none"]` after two checks in run 4 — not
a view bug. The durable state genuinely said that.

## Design

### 1. Populate the check aggregate

On the derived-records path, when the batch contains a `check_recorded` event, write
`latest_check_event_id`, `latest_subject_frontier_seq`, `latest_subject_frontier_digest`,
`latest_verdict`, `latest_returned_finding_ids`, `latest_suppressed_count`, and
`latest_coverage_canonical` from the in-memory projection the reducer already holds.

The values must come from the reducer's own state, not be recomputed from the wire result — the
durable mirror and the response must not be able to disagree.

`0001.sql:732` and `:742` already carry consistency constraints over `latest_check_event_id`
being null versus non-null. Honour them; do not relax them. If a partial write would violate a
constraint, that is the constraint doing its job and the projection is wrong.

### 2. Stop the last event from defining coverage

Status coverage must reflect the *applicable check's* coverage, not the envelope of whichever event
happened to land last. The reducer already computes the correct value —
`tests/integration/application/test_respond_status_receipt.py:876-891` asserts exactly this
behaviour at the application layer, with a comment naming it as the run-2 symptom. The durable
mirror does not implement it.

Write the projection's own coverage rather than `records[-1].coverage`, on **both** update paths —
the ordinary append path has the same bug and will exhibit it as soon as anything reads coverage
from the durable mirror after a restart.

### 3. Prove it survives a restart

The current defect is invisible in-process because the in-memory projection holds the truth. Every
test for this plan must read the durable mirror back — reopen the bundle, or assert directly
against `p1_projection_state` — otherwise it will pass against the bug.

## Files

- `src/yoetz/adapters/sqlite/repository.py` — both `UPDATE p1_projection_state` statements
- tests under `tests/integration/` that reopen the bundle and assert durable state

## Tests

- After a check, `latest_check_event_id`, `latest_verdict`, and `latest_coverage_canonical` are
  populated in `p1_projection_state`.
- After a check, `status_coverage_canonical.check_types` reflects the check — `["deterministic"]`,
  or `["deterministic", "semantic_model_derived"]` when semantic succeeded — and never `["none"]`.
- The same assertions after closing and reopening the bundle: the mirror, not the cache.
- A check followed by ordinary published work does not resurrect `["none"]`.
- The `0001.sql` consistency constraints hold for a task with no check, one check, and two checks.
- A task that has never been checked still reports `check_types: ["none"]` — the fix must not
  fabricate coverage.

## Done

Green CI, and a checked task's durable coverage reports what was actually checked.

## Dogfood observable

Run 5: after a successful `check`, `status view=versions` must report
`coverage.check_types` containing `deterministic` — and `semantic_model_derived` when semantic
succeeded — rather than `["none"]`.

## Out of scope

Populating the entity tables `p1_query_checks`, `p1_query_check_policy_executions`, and
`p1_query_check_returned_findings`. They are dead today and nothing observed depends on them; that
is a larger reducer change with no defect behind it yet. This PR fixes the aggregate and the
coverage lie only.

# 04 — End the unset-optional-becomes-null class

**Severity:** high **PR boundary:** every projection that can emit `null` for a non-null optional

**Reuses:** the result-model sweep harness built in [01](01-check-response-findings.md).

## The defect

An obligation published without `acceptance_criteria` breaks `status view=compact` — the **default
view** — and `status view=obligations`. The projection materializes the unset optional as explicit
JSON `null`, and the closed wire model rejects it.

This is the **third** sighting of one class. PR #50 fixed it for accepted-event `summary`; it
reappeared here in two more views. Fixing this instance alone would be the same mistake a third
time, so this plan closes the class.

## Evidence

Reproduced in-process on the real ready composition. An obligation with `description` and
`evidence_expectation` and no `acceptance_criteria` — exactly what run 4's agent published:

```
status compact      RAISED ValidationError: items.0.open_obligations.0
    Value error, optional_field_must_not_be_null
    input_value={'obligation_id': 'obl_…', …, 'acceptance_criteria': None}
status obligations  RAISED ValidationError: items.0
    Value error, optional_field_must_not_be_null
```

`versions`, `findings`, `history`, `evidence`, `advice`, `assignment`, and `candidate_findings`
project cleanly. Run 4 hit `compact` (`item_32`) and recovered via `versions`; it never called
`obligations`, so that half went unreported.

The guard is in `_ClosedModel` (`src/yoetz/protocol/models.py:816-823`):

```python
@model_validator(mode="before")
def _adapt_json_arrays_and_reject_forbidden_nulls(cls, value: object) -> object:
    for field_name in cls.optional_non_null_fields:
        if field_name in source and source[field_name] is None:
            raise ValueError("optional_field_must_not_be_null")
```

The contract is deliberate and correct: these fields admit text, an omission marker, or *total
absence* — never null. The projection is what is wrong.

The two models hit here are `StatusCompactObligationModel`
(`src/yoetz/protocol/models.py:1978-1994`) and `StatusObligationItemModel` (`:2235-2236`), both
declaring `optional_non_null_fields = frozenset({"acceptance_criteria"})`.

The in-memory ledger already does this correctly — `src/yoetz/adapters/memory/ledger.py:715-716`
and `:730-731` omit the key when the value is `None`. The path that runs in production does not.

## Design

### 1. Fix the obligation projection

Omit the key entirely when the source value is unset, rather than emitting `null`, and preserve
that omission through `_public_model`. This is the shape PR #50 established for accepted-event
summaries; apply the same discipline here.

### 2. Sweep every model that declares the constraint

Fourteen models declare `optional_non_null_fields`. For each, determine whether any projection can
emit `null` for a listed field, fix the ones that can, and pin every one with a test:

| Model | Fields |
| --- | --- |
| `ActorAssertionModel` | `asserted_by`, `display_name` |
| `SubjectStateRefModel` | `tree_digest`, `diff_digest`, `described_state` |
| `PublicErrorModel` | `safe_details` |
| `StartRequestModel` | `session_id`, `external_ref`, `workspace_ref` |
| `PublishWorkRequestModel` | `dry_run` |
| `CheckRequestModel` | `mode`, `scope`, `max_findings`, `policy_packs` |
| `RespondRequestModel` | (several) |
| `StatusAssignmentFilterModel` | `actor_id`, `include_resolved` |
| `StatusCandidateFindingsFilterModel` | `priority` |
| `StatusEvidenceFilterModel` | `freshness`, `include_unavailable`, `strength` |
| `StatusFindingsFilterModel` | `disposition`, `include_resolved`, `origin`, `priority` |
| `StatusHistoryFilterModel` | `actor_id`, `after_sequence`, `schema_name` |
| `StatusObligationsFilterModel` | `actor_id`, `include_resolved`, `status` |
| `StatusRequestModel` | `filter` |
| `PublishWorkAcceptedEventModel` | `summary` — fixed by PR #50; keep its test |
| `RespondEvidenceSummaryModel` | `description` |
| `RespondResponseModel` | `reason`, `waiver_scope`, `waiver_expiry` |
| `StatusCompactObligationModel` | `acceptance_criteria` — this defect |
| `StatusStructuralSubjectStateModel` | `tree_digest`, `diff_digest` |
| `StatusObligationItemModel` | `acceptance_criteria` — this defect |

Request models are caller-supplied and already reject null correctly; the audit still needs to
confirm no *internal* construction path can produce one. Result models are the real risk surface.

### 3. Make the class untestable-to-forget

Extend plan 01's sweep so that for every result model declaring `optional_non_null_fields`, a case
exists with each such field unset, projected end to end. A new model that declares the constraint
and has no such case should be visibly missing from the table.

## Files

- the obligation projection path feeding `StatusCompactObligationModel` and
  `StatusObligationItemModel`
- `src/yoetz/application/status.py` if the omission needs preserving through the view
- the sweep test module from plan 01

## Tests

- An obligation with no `acceptance_criteria` projects through `view=compact` and
  `view=obligations`.
- An obligation *with* `acceptance_criteria` still projects, with the text intact.
- An obligation whose `acceptance_criteria` is policy-omitted projects as an omission marker, not
  as absence and not as null — the three states stay distinguishable.
- For every result model declaring `optional_non_null_fields`, a case with each field unset
  projects.
- The `_ClosedModel` guard still rejects an explicit null — this plan fixes producers, and must not
  weaken the contract.

## Done

Green CI, and no projection can emit `null` for a field the wire contract forbids it on.

## Dogfood observable

Run 5: `status view=compact` must succeed on a task with an open obligation. No
`read_projection_failed` on any status view.

## Out of scope

Changing which fields are optional, or the omission-marker contract. This plan makes producers obey
the existing contract.

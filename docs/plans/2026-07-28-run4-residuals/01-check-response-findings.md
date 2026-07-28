# 01 — `check` must be able to return a finding

**Severity:** critical **PR boundary:** public result-model projection + a result-model contract sweep

## The defect

`check` cannot return any response that contains a finding. Not one. In any mode.

The check commits durably — `check_recorded` and `finding_recorded` land in the ledger and the
frontier advances — and then the response fails to project, so the caller receives
`INTERNAL_ERROR` / `response_projection_failed` and never learns the verdict, the finding, or the
semantic outcome.

This is the single most damaging defect in the product. `check` exists to tell an agent what is
wrong with its work, and the delivery channel for that answer is `findings[]`. It has never worked.

**It is also the sole blocker on semantic advice.** Semantic review returns its explanation as
findings. The two semantic successes ever observed both returned `findings: []`, which is the only
reason this stayed hidden — they had nothing to carry. See
[02 — semantic advice delivery](02-semantic-advice-delivery.md).

## Evidence

Reproduced in-process on the real ready composition (real vault, real SQLite, real privacy
projection) across every mode. The only variable is whether a finding exists:

| Config | Mode | Findings | Check projection |
| --- | --- | --- | --- |
| `semantic = disabled` | `deterministic_only` | 2 | **RAISED** |
| `semantic = optional` | `semantic_required` | 2 | **RAISED** |
| `semantic = optional` | `semantic_if_configured` | 2 | **RAISED** |
| any | any | 0 | ok |

```
ValidationError: 2 validation errors for CheckSuccessModel
findings.0
  Input should be a valid dictionary or instance of CheckProjectedFindingModel
  [type=model_type, input_value=JsonObject((('finding_id'…)), input_type=JsonObject]
findings.1
  … same …
```

Raised at `src/yoetz/application/service.py:490`, inside `_public_model`:

```python
success = success_type.model_validate(value)
```

`_ClosedModel` is configured `strict=True` (`src/yoetz/protocol/models.py:409`). Under strict mode
pydantic accepts only a real `dict` (or an instance of the target model) for a nested model field.
The internal result carries nested entries as `JsonObject` — a Mapping, but not a `dict` — so every
nested element is rejected. Top-level fields survive because they are scalars.

Also confirmed live end to end through the running service on 2026-07-28. One task, two checks,
identical mode, the finding as the only difference:

- Check A, clean work: `ok=True`, `verdict=no_issue_detected`, `findings=0`,
  `semantic: succeeded / semantic_completed`, full provenance, complete response.
- Check B, same task plus one `evidence_does_not_support_claim` finding:
  `internal_error: the local request could not be completed`, and in the durable sink
  `service.daemon | check_response_projection_failed | exception_validation_error`.

Why three prior dogfoods missed it: **no check had ever produced a finding.** Run 3's semantic
check passed only because `findings: []`.

## Design

### 1. Normalize nested mappings once, at the projection boundary

In `_public_model`, convert the internal JSON value to plain `dict`/`list` recursively before
`model_validate`. One place, above every result model, so no individual serializer has to remember.

Constraints:

- The conversion is structural only. It must not coerce scalars, reorder keys, drop keys, or
  substitute defaults — a shape that is genuinely invalid must still be rejected, with the same
  error it raises today.
- It must not weaken `strict=True`. Relaxing strictness on the closed models is the wrong fix: it
  would start accepting int-for-string and other coercions across the whole public contract.
- It must be bounded — the existing depth and size limits on internal results still apply, and a
  pathological structure must degrade to a rejection rather than unbounded recursion.

### 2. Close the class with a result-model sweep

PR #50 fixed one instance of a projection defect and the class survived to reappear twice. Do not
repeat that. Add a sweep test that walks every public result model with a nested collection and
asserts each projects from a realistic internal result:

- `check` — `findings`, `policy_executions`
- `publish_work` — `accepted_events`
- `status` — every view's `page.items`, plus the nested `open_obligations` and
  `unresolved_findings` inside a compact item
- `respond` — `accepted_event`, `evidence`
- `receipt` — its nested slices
- `start` — `compact`

This harness is reused by [04 — null optional sweep](04-null-optional-sweep.md), so build it to be
extended rather than as a one-off.

## Files

- `src/yoetz/application/service.py` — `_public_model`, the normalization
- `tests/integration/application/` — the check-with-findings regression
- a new sweep test module covering every public result model with a nested collection

## Tests

- A `check` returning one finding projects to a complete `CheckSuccessModel`; assert the verdict,
  the finding id, kind, origin, priority, `summary`, `detail`, and `subject_refs` all survive.
- The same in `deterministic_only`, `semantic_if_configured`, and `semantic_required` — the defect
  is mode-independent and the test must say so.
- A check returning the maximum permitted findings projects.
- A genuinely malformed nested entry is still rejected, with an error naming the same pointer as
  today. Normalization must not become a shape-laundering step.
- The sweep: every public result model with a nested collection projects.
- No test may assert on `JsonObject` specifically — the invariant is "nested mappings project",
  not "this one internal type is handled".

## Done

Green CI, and a `check` that produces findings returns them.

## Dogfood observable

Run 5 must show a `check` returning at least one finding to the agent, with its `summary` and
`detail` text. No `check` may return `INTERNAL_ERROR` / `response_projection_failed`.

## Out of scope

Why a check produces the findings it produces. Semantic delivery and non-dispatch reporting
(plan 02). The durable projection of check state (plan 03).

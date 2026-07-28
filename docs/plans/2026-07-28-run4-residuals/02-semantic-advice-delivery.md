# 02 — The second reviewer must deliver its advice, or say why it did not

**Severity:** critical **PR boundary:** semantic finding projection + non-dispatch observability + binding freshness

**Depends on:** [01](01-check-response-findings.md) for the delivery channel. Until 01 lands, no
semantic finding can reach an agent regardless of what this plan does.

## Why this matters

Semantic review is a second agent reading the frozen case and returning *explanation and advice* —
the reasoning a deterministic policy cannot produce. It is the difference between "policy
`work-integrity` fired rule X" and "your evidence is metadata-only, so this completion claim
overstates what you verified; cite the digest or narrow the claim."

That value is delivered in exactly one place: a finding with `origin: "semantic_model_derived"`
carrying `summary`, `detail`, and `provenance`. **That path has never executed.**

## The defect, in two parts

### Part A — the semantic finding path is unexercised

Dispatch working and advice arriving are different things, and only the first has ever been proven.

Every observed semantic success returned `findings: []`:

| Run | Semantic outcome | Findings |
| --- | --- | --- |
| Run 3 `item_62` (2026-07-27) | `succeeded / semantic_completed`, `provider_request_id: resp_97b64b05…` | **0** |
| Live Check A (2026-07-28) | `succeeded / semantic_completed`, `provider_request_id: resp_357677ac…` | **0** |
| Live Check A, second execution | `succeeded / semantic_completed`, `provider_request_id: resp_a7ff5189…` | **0** |

So `validate_semantic_judgment` → `allocate_findings` → `CheckProjectedFindingModel` with
`origin: "semantic_model_derived"` and non-null `provenance` has never run against a real provider
response. `CheckProjectedFindingModel` enforces a hard pairing
(`src/yoetz/protocol/models.py:1654-1657`):

```python
if self.origin == "deterministic" and self.provenance is not None:
    raise ValueError("deterministic_finding_provenance_invalid")
if self.origin == "semantic_model_derived" and self.provenance is None:
    raise ValueError("semantic_finding_provenance_missing")
```

Neither branch has ever been exercised end to end. Given plan 01's history — a strict-mode
rejection that sat latent for four dogfoods because nothing ever populated the array — the semantic
finding must be proven, not assumed.

### Part B — semantic can decline to run and record nothing

There are three paths on which semantic does not dispatch. Two are completely silent: no job, no
diagnostic, no log line, nothing durable.

| Path | Where | Reported outcome | Records anything? |
| --- | --- | --- | --- |
| `provider is None` | `ready_composition.py:1413` | `not_configured / provider_not_configured` | **no** |
| route absent or not `ACTIVE` | `ready_composition.py:1416-1417` | `not_configured / provider_not_configured` | **no** |
| binding unresolved at composition | `ready_composition.py:1635` | `unavailable / credential_unavailable` | **no** |
| evaluator raises | `ready_composition.py:1478-1490` | `failed / coordinator_failure` | yes — `semantic_composition` / `semantic_evaluation_failed` |
| evaluator raises above the composition | `check.py:836` bare `except Exception` | `failed / coordinator_failure` | **no** |

In run 4 the durable sink contains **zero** `semantic_composition` records, so the one instrumented
path did not fire — it was one of the silent ones, and which one is unrecoverable.

The only place the answer would have surfaced is `semantic_status` / `semantic_reason` in the check
response, which plan 01's defect destroyed; the durable second copy in `p1_query_checks` was never
written because of plan 03's defect. **Two independent defects erased the reason.**

### Part B2 — the binding is resolved once and never revisited

`provider_binding` is computed at service composition (`ready_composition.py:1554-1563`):

```python
if candidate_binding.provider_id in connected_provider_ids:
    provider_binding = candidate_binding
provider_credential_connected = provider_binding is not None
```

`connected_provider_ids` reads the generation-fenced registry at that instant. If the vault or the
credential registry is not ready at composition time, semantic is dead for the **entire service
generation**, silently, and unlocking afterwards does not revive it. `yoetz provider status` will
keep reporting the endpoint as bound, so nothing surfaces the difference.

## Design

### 1. Prove the semantic finding path

An integration test that drives a semantic judgment through `validate_semantic_judgment`,
`allocate_findings`, and the public projection, asserting a finding arrives with
`origin: "semantic_model_derived"`, non-null `provenance`, and its `summary` and `detail` intact.

Use a deterministic stand-in for the provider so the test is hermetic — the point is the
projection contract, not live dispatch. Additionally assert the negative pairing: a semantic
finding without provenance is rejected, and a deterministic finding carrying provenance is
rejected.

### 2. Every non-dispatch path records a bounded diagnostic

Add `record_unexpected_exception_without_raising`-style emission — or the equivalent bounded
diagnostic write — to each silent return, with an operation token naming the path:

- `semantic_not_dispatched_provider_unbound`
- `semantic_not_dispatched_route_inactive`
- `semantic_not_dispatched_credential_unavailable`
- `semantic_not_dispatched_coordinator_failure` (the `check.py` bare `except`)

Same discipline as the existing sink: `correlation_id`, `component`, `operation`, bounded reason
token, `request_id`, timestamp. **No exception text, no payload, no provider identity, no paths.**

The `check.py:836` bare `except Exception` must stop discarding its cause. Keep the fail-closed
behaviour — it must still never fabricate a clean semantic pass — but record the bounded reason
token before returning `FAILED / COORDINATOR_FAILURE`.

### 3. Say why, in the response

When semantic does not run, the agent should be able to act on it. `semantic_status` and
`semantic_reason` already carry this and `semantic_coverage_gap_code` already adds the gap to
`coverage.known_gaps` — both become visible the moment plan 01 lands. This plan adds no new wire
field; it adds a test that pins the pairing for each non-dispatch reason so the agent-visible
answer cannot silently regress.

### 4. Re-resolve the provider binding

Decide and implement one of: re-resolve `provider_binding` at check time rather than caching it
from composition, or invalidate the composition-time value when the vault or credential generation
changes. Whichever is chosen, a service that composes with a locked vault and is unlocked
afterwards must be able to dispatch semantic without a restart, and a test must assert it.

## Files

- `src/yoetz/service/ready_composition.py` — the silent returns, the binding resolution
- `src/yoetz/application/check.py` — the bare `except` at `_semantic_evaluation`
- `src/yoetz/observability/diagnostics.py` — no change expected; reuse the existing sink
- tests under `tests/integration/` for the semantic finding projection and each non-dispatch path

## Tests

- A semantic finding projects with `origin: "semantic_model_derived"`, non-null `provenance`, and
  its `summary`/`detail` text.
- A semantic finding missing provenance is rejected; a deterministic finding with provenance is
  rejected.
- Each of the four non-dispatch paths produces its distinct bounded diagnostic **and** its exact
  `semantic_status` / `semantic_reason` pair and coverage gap code.
- A diagnostic carries no exception text, no payload, no provider identity, no filesystem path.
- Composing with an unavailable credential and then making it available lets a later check
  dispatch, with no service restart.
- `semantic_required` with a non-dispatching semantic path still yields a *projectable* response
  reporting the reason — the agent is told why, not given an internal error.

## Done

Green CI, a semantic finding demonstrably projects, and every path on which semantic declines to
run leaves a durable, resolvable record.

## Dogfood observable

Run 5 must be built so semantic has something to say. The signal: at least one finding with
`origin: "semantic_model_derived"` delivered to the agent with its explanation text and its
`provider_request_id` provenance. If semantic does not run, the agent must be told which of the
named reasons applied, and the sink must contain the matching record.

## Out of scope

The delivery channel itself (plan 01). Persisting check state durably (plan 03). Changing what the
semantic policy asks the model, or the prompt.

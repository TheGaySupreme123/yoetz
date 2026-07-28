# 03 — Make the event batch authorable on the first attempt

**Severity:** critical **PR boundary:** MCP authoring hints, schema examples, dry-run mode, guidance surfacing

## The defect

Nine of seventeen Yoetz MCP calls in run 3 surfaced as failures. Most were authoring mistakes, not
runtime faults — but authoring friction is the dominant cost of using the product, and it is the
proximate cause of the unreachable replay in plan 03.

The hint system already exists and provably works at depth 1. It fails at depth 2, which is where
every expensive mistake happened.

## Evidence

All eight `INVALID_REQUEST` results from `docs/dogfood/2026-07-27-grok-easy-linking/codex-events.jsonl`:

| # | Tool | Hint quality | Outcome |
| --- | --- | --- | --- |
| 1 | `start` | `protocol_version admits 0.1; schema_version admits 1.0.0; request_id admits ^req_…` | fixed next call |
| 2 | `start` | `mode admits attach, create, create_or_attach` | fixed next call |
| 3 | `publish_work` | full envelope hint | fixed next call |
| 4 | `publish_work` | **`/event_drafts/0` — "see the examples entry"** | cost a source-reading detour |
| 5 | `publish_work` | `request_id admits ^req_…` | fixed next call |
| 6 | `publish_work` | **`/event_drafts/2` — "see the examples entry"** | killed replay attempt 1 |
| 7 | `publish_work` | **`/event_drafts/3` — "see the examples entry"** | killed replay attempt 2 |
| 8 | `receipt` | `writer_id admits ^wri_…` | fixed next call |

Every hint that named admitted values was fixed on the next call. Every hint that said "see the
examples" cost real work. The boundary is explicit in code —
`src/yoetz/mcp/errors.py:185-188`:

> Only top-level scalar fields; nested pointers would need the `$defs` walk and the example already
> shows their shape.

The example does not show their shape well enough. Three dogfoods disagree with that comment.

Separately: **the four packaged guidance documents were never read once.** There are zero MCP
resource reads in the run-3 trace. The single `yoetz://guidance` string in the trace is the agent
reading `docs/INTERFACES.md` off disk. The product ships harness-neutral guidance and then waits to
be asked for it, and nobody asks.

## Design

Four levers, all in this PR. They are cheap individually and reinforcing together.

### 1. Walk `$defs` for nested pointers

Extend `authoring_hint` past the `pointer.count("/") != 1` guard so `/event_drafts/2` resolves
through the array item schema, the discriminated event union, and the payload `$defs` to name
admitted values — including the nested `action_kind` enum the postmortem called undiscoverable.

**Preserve the safety invariant.** The current docstring is the contract: *"Every character comes
from the checked-in presentation schema — enum members and the worked example's own keys — so no
caller-controlled text can reach the message."* The walk must resolve only local `#/$defs/`
references, must never echo a submitted value, and must stay bounded by the existing
`_MAX_HINT_FIELDS`, `_MAX_HINT_ENUM_MEMBERS`, and `_MAX_HINT_PATTERN_CHARS` caps. A test must assert
that a hostile payload value cannot reach the message.

Where a union discriminator is what failed, name the admitted schema names. Where the discriminator
resolved and a payload field failed, name that field's admitted values.

### 2. A worked example per event family

One complete, valid, checked-in example for each event schema — `plan_published`,
`action_recorded`, `result_recorded`, `evidence_recorded`, `claim_recorded`, `decision_recorded`,
`obligation_published`, `response_recorded` — reachable from the input schema and referenced by name
from the hint. "See the examples entry" is only useful advice when the entry covers the family that
failed; today it shows the envelope.

Include a cross-referencing example: an action, its result, evidence, and a claim whose
`supporting_refs` point at the earlier ids. Cross-event reference construction is exactly what the
run-3 batch was doing and there is no worked example of it anywhere.

### 3. Dry-run validation

Accept `dry_run: true` on `publish_work`. It validates the full batch and returns what would be
accepted — event ids, resolved schemas, references, coverage — and appends nothing.

Constraints:

- no ledger append, no operation record, no `request_id` consumption, no frontier movement;
- the result must be explicitly non-evidential and must not be citable as a check, publication, or
  coverage source — same discipline as `status view=candidate_findings`;
- rejects with the same hint machinery as a real publish, so an agent can iterate at zero cost.

This turns authoring from "guess, fail publicly, retry" into "validate, then publish."

### 4. Push guidance instead of waiting for it

- Each tool description names the guidance resource that covers it, by URI.
- An `INVALID_REQUEST` on `publish_work` includes the `yoetz://guidance/publication-policy.md` URI;
  `start` and workflow-shaped errors point at `yoetz://guidance/workflow.md`.
- Only the URI and packaged, manifest-verified content — no synthesized prose in the error path.

## Files

- `src/yoetz/mcp/errors.py` — `authoring_hint`, `$defs` resolution, safety invariant
- `src/yoetz/mcp/server.py` — tool descriptions, guidance URIs in error paths
- `src/yoetz/mcp/resources.py` — surfacing
- `src/yoetz/application/publish_work.py`, `src/yoetz/protocol/models.py` — `dry_run`
- presentation schemas under `schemas/` and `src/yoetz/resources/schemas/` — worked examples
- `fixtures/`, `docs/INTERFACES.md`, `guidance/publication-policy.md`, resource manifest digests

## Tests

- For each event family, an invalid draft at `/event_drafts/N` produces a hint naming admitted
  values, not the generic fallback.
- A nested enum failure (`action_kind`) names its admitted members.
- Hostile payload values never appear in a hint message.
- Hints stay within all existing bounds; a deeply nested or oversized schema degrades gracefully to
  the current fallback rather than raising — a hint must never turn a clear validation error into an
  internal error, which the existing `except Exception` at `errors.py:135-138` already guarantees
  and a test must keep guaranteeing.
- `dry_run: true` appends nothing: frontier unchanged, no operation record, `request_id` still
  usable for a real publish afterwards.
- Every worked example validates against its own schema — a stale example is worse than none.
- Guidance URIs in tool descriptions and errors resolve to real registered resources.

## Done

Green CI, and each of the three run-3 `/event_drafts/N` failures produces an actionable hint.

## Dogfood observable

Run 4's failed-call count should drop sharply from 9 of 17. The specific signal: **no
`publish_work` failure whose hint is the bare "see the examples entry" fallback.** A secondary
signal worth watching — whether the agent reads a guidance resource at all, now that one is named.

## Out of scope

Changing what events mean or the publication policy itself. This PR makes the existing contract
authorable; it does not simplify it.

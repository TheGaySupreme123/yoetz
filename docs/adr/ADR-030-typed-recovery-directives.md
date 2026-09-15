# ADR-030 — Typed recovery directives for public errors

**Status:** Proposed for issue #739; the maintainer requested this scoped work on 2026-09-15,
including the four-tier classification, the evidence-driven subset plus ratchet, and the
pointer-with-directive decision. Surface coverage beyond MCP and CLI remains a review decision on
that issue.

**Relates to:** ADR-002, ADR-009, ADR-015, ADR-018, and issues #739, #740, #669, #741, #742.

## Context

An agent that hits a Yoetz error is told *that* something failed, not *what to do next*. The public
error identity — code, retryable flag, correlation ID — survives every surface. The recovery
instruction does not.

This is not a consequence of the egress fence. The fence is correct and stays: `CONTRIBUTING.md`
locks "nothing user-controlled ... appears in ... errors, or MCP text summaries", and
`summary_for_public_error` therefore projects only allowlisted structural content and never the
free-form `message`.

The defect is that facts which are **not user-controlled at all** were composed into prose at the
raising site, and prose is fenced wholesale. Two observations establish this:

- The typed `vault_initialization_required` continuation of ADR-015 and issue #512 carries frozen
  repository command literals in `safe_details`. Native text dropped all of them, so a flow
  explicitly designed not to dead-end reached the model as a dead end (issue #740).
- `_claim_revision_clause` recovered an invariant by regex-matching the service's own English
  message, then looked that invariant up in a closed correction table. The information had been
  structurally available three layers earlier.

Twelve closed issues repaired this one error class at a time (#266, #240, #579, #237, #220, #428,
#308, #342, #335, #326, #147, #93). The recurrence is the argument for a contract.

## Decisions

### The token travels; the text does not

A public error carries a typed `continuation` token in `safe_details`. Each renderer reconstructs
the directive text, guidance pointer, and nudge locally from a checked-in registry keyed by that
token.

Consequences that follow, and are the reason for this shape:

- Directive text is never data. It cannot be truncated, re-encoded, or replayed by a peer, and a
  compromised or buggy producer cannot author error prose that a renderer will repeat. This is the
  decisive reason: it is a property no additional wire field can provide.
- Prose changes cost nothing on the wire. Rewording a directive is a one-line registry edit with no
  schema, fixture, or golden-vector churn, and every surface moves together by construction.
- A third-party consumer reading structured JSON receives the token and resolves it through
  `docs/INTERFACES.md`. Serving such a consumer prose instead would mean versioning the schema for
  text that its own renderer could reconstruct.

**Not** a reason, recorded because an earlier draft of this ADR asserted it: the 32-key
`SAFE_DETAIL_KEYS` allowlist was not at a schema ceiling. `public-error-1.0.0` sets
`maxProperties: 32` on a `safe_details` *instance* and admits property names by pattern rather than
by an enumerated list, so the code-side allowlist may exceed 32 without a version bump. ADR-030
itself then added a 33rd key, `invariant`, under that unchanged schema. Adding a typed detail is
therefore a live option for facts a consumer genuinely needs structurally; it is simply the wrong
carrier for prose.

### The token is attached where the error is built

The first implementation mapped reason codes to tokens but attached them only in one MCP timeout
branch, so a real `unsorted_set_field` rejection reached the model with the registered directive
absent. A reason code determines its directive regardless of which producer raised it, so
`PublicOperationError` attaches the mapped token at construction, from a map held literally in the
dependency root beside the admitted token set. A producer that already chose a continuation is
never overridden; that is how the one boundary with more information than the reason (the MCP
bridge, for timeouts) says so. Raising sites still classify Tier-4 facts into tokens; they no
longer have to remember to attach the token a reason already implies.

### Rejected input is a correction, not a retry

`retryable: false` means "do not resend this body". It never meant "stop": a body the schema
validator refused before any write can only be fixed by sending a different body. The shipped
safety floor said only the former, and an agent that read it literally treated a malformed
`actor_id` as terminal. The bridge's argument validator, the sole producer of location-shaped
`safe_details`, therefore attaches `input_correction_new_identity`, whose directive names the
identity rule (a new `request_id`, once) and distinguishes the case from an ambiguous write. A
unique field-ownership repair and an unreachable recovery oracle are the two facts that override it
with their own directives. The guidance floor now states the same rule in the same words.

### A timed-out `start` is its own kind

The generic write directive (read `status view=operation`, replay only on `absent`) requires the
session and writer ids that a lost first `start` never returned. The recovery table has always
excepted that case with one exact replay under the same `request_id`; the directive registry now
carries the exception as `start_timeout_same_identity` rather than handing a first start a write
directive it cannot follow.

### Four tiers, classified at the raising site

Every fact in an error belongs to exactly one tier, decided where it is known:

| Tier | Example | Rule |
| --- | --- | --- |
| 1. Frozen directive | "Repeat the read with a NEW request_id." | Registry value keyed by token. |
| 2. Structural locator | `/event_drafts/0/payload/obligation_refs` | Allowlisted frozen schema vocabulary. |
| 3. Gated runtime value | `req_…`, `sha256:…`, counts, versions | Pattern-gated in `normalize_safe_details`. |
| 4. Never | payloads, paths, titles, provider and model output, raw exception text | Classified into a Tier-1 token at the raising site, then discarded. |

Tier 4 is load-bearing and is why this costs more code than echoing a message: classification
happens at each raising site rather than once at a renderer. What it buys is exhaustive
import-time testability, which prose cannot have.

### A directive is an instruction, never a prediction

"Read status view=operation before replaying" is admissible: the error establishes the condition.
"This will fix it" is not: the error cannot substantiate an outcome, and coverage-bounded language
forbids claiming one. Nudges attach per error class, so a schema-validation failure and a vault
refusal never receive the same unhelpful advice.

Guidance pointers are emitted only where a matching guidance section exists, and every pointer is
tested to resolve to a real document and heading. A pointer that leads nowhere teaches an agent to
ignore every pointer.

### Two disjoint reason vocabularies

Protocol reason codes (`PROTOCOL_REASON_CODES`) and local lifecycle, instance, and ceremony reasons
(`yoetz.cli.exits`) are separate namespaces and resolve through separate lookups. An overlap is an
import-time failure. Conflating them is a real hazard: three CLI lifecycle reasons were nearly
registered as protocol reasons while the registry was first written, and the gate is what caught it.

### Coverage grows by ratchet

Directives are populated for reason codes with demonstrated agent impact rather than by one
exhaustive pass. Every remaining reason code carries an explicit exemption, recorded as a decision:
a validation reason whose field pointer already names the repair, an internal invariant an agent
cannot act on, or a family owned by a later sub-issue.

An import-time gate requires every reason code to resolve to a directive or appear on that
exemption list, and requires the registry's token set to equal the set `normalize_safe_details`
admits. Adding a reason code without deciding what an agent should do fails the build.

### Budget priority

The MCP text channel is bounded at 512 ASCII bytes and raises rather than truncating. Projection
order is: error identity, then reason and location, then continuation directive, then carried
commands, then guidance pointer, then nudge. Parts are dropped from the bottom. Error identity is
never sacrificed to fit advice.

## Consequences

- Recovery rules now exist in two places — `guidance/*.md` and this registry — and must move
  together. The guidance-anchor test couples them; a directive that contradicts its own guidance
  section is a documentation bug, not a rendering one.
- The `frontier_refresh_required` directive follows the shipped replay semantics: `publish_work`
  stores a frontier conflict as a retryable failure under the original `request_id`, and the
  producer's own message directs an idempotent retry under that same id. A directive that told the
  agent to mint a new `request_id` would have contradicted the message beside it.
- Provider and semantic failures gain typed failure tokens (issue #742) rather than an exemption
  from the Tier-4 rule. Some diagnostic nuance is genuinely lost at the agent-facing boundary and
  remains recoverable locally through the `correlation_id`, which never crosses the wire. That is
  the intended trade.
- `_claim_revision_clause` is retired. `ClaimRevisionMismatch` already carried a closed-set
  `invariant`, which the builder placed only inside the message, so the MCP projector matched that
  whole sentence with a regex to recover it and validated the result against a second copy of the
  domain's frozenset. The invariant is now an allowlisted safe detail, the regex and the duplicated
  vocabulary are deleted, and the corrective phrases moved into the shared registry — which also
  gave the CLI a correction it never rendered. The clause no longer depends on message integrity,
  so rewording the message costs an agent nothing.

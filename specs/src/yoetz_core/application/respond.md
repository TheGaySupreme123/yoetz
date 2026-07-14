# src/yoetz_core/application/respond.py — attributable finding acknowledgement, rejection, and waiver

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-005 | **Imports
(spec-tree):** `protocol/models.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`protocol/errors.md`, `domain/events.md`, `domain/findings.md`, `domain/values.md`,
`ports/ledger.md`, `ports/objects.md`, `ports/clock.md`, `ports/ids.md`,
`application/unit_of_work.md` | **Imported by:** `application/service.md`

## Purpose

`respond` records what an attributable actor did with a finding: acknowledge it, reject it with a
reason, or waive it under explicit authority/scope. It never edits or erases the finding and never
conflates acknowledgement with remediation. The operation freezes both the finding version the
caller saw and the current append frontier so a response cannot silently attach to changed state.

## Public surface

- `async execute_respond(app: Application, request: RespondRequest) -> RespondResult` —
  implementation behind `Application.respond`.

Request/result wire values remain in `protocol/models.py`; this file defines no transport-specific
shape and no second response vocabulary.

## Behavior

### Validate the response

1. Resolve the validated session/writer pair to one active task runtime. Constrain caller actor
   assurance from the integration channel; an MCP display name or asserted actor type supplies no
   extra authority.
2. Require `disposition` to be exactly `acknowledged`, `rejected`, or `waived`. `reason` is
   optional for `acknowledged` and required, non-empty, and at most `MAX_REASON_BYTES` for
   `rejected`/`waived`. When present it is user content and enters only the encrypted payload
   object.
3. For `waived`, require `waiver_scope=finding_only`; permit optional `waiver_expiry` only with
   waiver and parse it as the exact protocol timestamp. For the other dispositions, scope and
   expiry are forbidden. Waiver is accepted only from an interactive local-CLI request constrained
   as human after explicit confirmation; MCP, importer, noninteractive CLI, and model-backed
   actors cannot waive. An already expired waiver has no current effect but remains history.
4. Normalize `evidence_refs` as bounded, sorted-unique references. Every referenced evidence/result
   must exist in the same task by the append frontier and remain structurally available; no
   cross-task or future reference is accepted.

### Bind to the shown finding

1. Interpret boundary `finding_frontier` as the exact stable ledger frontier at which the caller
   was shown this finding. Load/replay through that frontier and prove the named `finding_id`
   existed and was visible there with the same immutable finding event. A later finding with a
   similar summary or subject is not a match.
2. Require `expected_frontier` to equal the task head immediately before append. This independently
   protects current response context: `finding_frontier` says “what I am responding to,” while
   `expected_frontier` says “the current state against which I am appending.”
3. Preserve the full `Frontier` (sequence and head digest) in `ResponseRecordedPayload`. If the
   wire carries only the canonical sequence string, resolve and verify the head digest from the
   canonical ledger rather than fabricating or copying the current head.

### Prepare and commit one event

1. Build the canonical logical request digest from all caller-owned response semantics: protocol
   and schema versions, request/session/writer IDs, expected and finding frontiers, constrained
   actor/client, disposition, reason commitment, waiver fields, evidence references, and active
   authority-policy version/digest. Exclude the generated event/object IDs, response timestamp,
   encryption randomness, and ledger-assigned fields.
2. Construct one `ResponseRecordedPayload` with the exact finding frontier and validated fields.
   The accepted event author is the constrained caller actor, publication channel is derived from
   the client integration, and coverage can be no stronger than that channel. Generate the
   internal event ID, capture `occurred_at` as metadata, canonicalize, encrypt, and finalize the
   payload object outside any SQLite transaction.
3. Submit one-entry `AppendCommand(operation_kind=respond)` through `LedgerPort.append_batch` with
   the caller's `expected_frontier`. The append transaction repeats idempotency/current-frontier
   checks, accepts the object and event, updates the response projection, stores the structural
   terminal result, and commits atomically.
4. Return `RespondResult` only after commit, including disposition/finding IDs, the frozen finding
   frontier, pre-event subject frontier, post-event result frontier, event acceptance summary,
   coverage, and bounded warning codes. Same request ID and logical digest returns the stored
   original event/result; no new object or event is acknowledged.

The generated event ID is an internal consequence of the public response request. If a process
dies before the append creates an operation row, retry may allocate a different uncommitted ID; if
commit occurred, operation replay returns the original. The ID itself is therefore excluded from
logical request identity.

### Projection meaning

- `acknowledged` records that the actor saw/accepted the finding as requiring attention. It does
  not resolve the underlying obligation, prove a fix, or suppress the finding.
- `rejected` records disagreement and its stated basis. It remains visible in status and receipts;
  it does not rewrite finding origin, priority, or evidence.
- `waived` records a policy-authorized exception with scope and optional expiry. It never deletes
  the finding. Expired or out-of-scope waivers cease to affect current disposition but remain in
  history.
- Only new accepted work/evidence plus a later check can establish that the underlying condition
  is no longer present. Deterministic policy may emit `weak_or_stale_response` when the rejection,
  waiver, authority, evidence, scope, or subject state is weak/stale.

## Errors and edge cases

- Unknown finding, finding absent at the supplied shown frontier, malformed reason/disposition,
  forbidden waiver fields, invalid reference, or unauthorized waiver → `INVALID_REQUEST` with an
  allowlisted reason; no user text is echoed.
- Current head different from `expected_frontier`, or a sequence that resolves to a different
  canonical finding frontier → `FRONTIER_CONFLICT`. Same request ID/different logical request →
  `IDEMPOTENCY_CONFLICT`.
- Session/writer route absence or mismatch → `SESSION_NOT_FOUND`/`SESSION_CONFLICT`; contention,
  unsafe generation, corruption, key/object failure map through the registered storage errors.
- Cancellation/object failure before commit returns no success and may leave only an orphan
  encrypted payload. Cancellation during an ambiguous commit is resolved by the durable operation
  row and same-ID retry.
- A waiver authority rule changing between preparation and commit is caught through the request's
  policy digest/frontier dependency; old policy cannot silently authorize a new response.

## Invariants

1. A response references one immutable finding as shown at one exact canonical frontier.
2. No response mutates, hides, resolves, or strengthens the original finding.
3. Waiver effect is bounded by recorded authority, scope, and expiry; caller assertion alone never
   supplies authority.
4. Exactly one `response_recorded` event is acknowledged for one terminal response operation.
5. No network call or plaintext payload work occurs inside the append transaction.
6. Same-ID replay returns the original accepted event and frontiers byte-for-byte.

## Tests

- `specs/tests/unit.md`: disposition/reason/waiver matrix, authority policy, evidence-reference
  validation, finding/current frontier distinction, request digest exclusions.
- `specs/tests/conformance.md`: exact-frontier historical lookup, replay/conflict, one-event append,
  projection/receipt visibility, memory/SQLite result parity.
- `specs/tests/integration.md`: stale current frontier, later similar finding, expired/out-of-scope
  waiver, key/object/storage failures.
- `specs/tests/subprocess.md`: kill before object publication, before/during/after commit and before
  response; same-ID retry yields at most one response event.

## Open questions

None.

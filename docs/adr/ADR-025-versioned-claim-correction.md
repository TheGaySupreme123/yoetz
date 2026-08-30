# ADR-025 — Versioned append-only claim correction

**Status:** Accepted (2026-08-29), acknowledged in
[issue #432](https://github.com/TheGaySupreme123/yoetz/issues/432).
**Implemented by:** `src/yoetz/domain/events.py`, `src/yoetz/kernel/claims.py`,
`src/yoetz/kernel/reducers.py`, the deterministic policy packs, semantic-case and receipt builders,
and the public claim schema, guidance, descriptors, and conformance suites.
**Relates to:** ADR-002 (canonical protocol), ADR-019 (declared completion scope), and ADR-020
(typed evidence digest provenance).

## Context

`claim_recorded/1.0.0` uses one `supporting_refs` list for both evidence that supports a conclusion
and a partial or failed result that limits it. A later claim can dispute the earlier claim, but
`disputes_refs` deliberately records an unresolved contradiction; it is not supersession. A
decision can supersede another decision event, but not a claim. Trial-and-error correction
therefore appends more contradictions without providing a typed current replacement.

The failure is amplified by task-global limitation scans: a result recorded after a claim, or for
a disjoint obligation, can make that historical claim and unrelated later claims look incomplete.
Append-only history must remain intact, so mutation or erasure is not an acceptable repair.

## Decisions

1. **Claim correction is a new event-schema version.** `claim_recorded/1.1.0` requires
   sorted-unique `supersedes_claim_refs` containing claim ids and `limitation_refs` containing
   result ids; a producer sends an empty array when a field does not apply.
   `supporting_refs` retains its v1 meaning. Frozen `claim_recorded/1.0.0` bytes and decode behavior
   remain unchanged.

   The authoring path is additive too: `event-draft/1.1.0` adds the exact new pair,
   `opaque-unknown-event-draft/1.1.0` excludes it from opaque admission, and
   `publish-work-request/1.1.0` selects that union. Their 1.0 predecessors remain byte-identical.
   The frozen v1.0 union still admits the future pair as opaque; local control 2.4.0 embeds the
   exact-known request, while the manifest-bound handshake prevents a stale service from receiving
   it as an ordinary publication.

2. **Replacement is explicit and whole-claim.** A replacement uses a fresh claim id, names one or
   more prior effective claims of the same kind, and declares obligation scope overlapping every
   target. The replacement becomes the effective current claim; the targets remain immutable in
   projection history. Narrowing a claim may deliberately leave former scope without a current
   completion claim; no scope is silently transferred.

3. **Limitations are separate from support.** A v1.1 completion claim puts every relevant partial
   or failed result in `limitation_refs`; such a result is rejected from `supporting_refs`.
   A relevant result existed no later than the claim and either has unscoped action provenance or
   overlaps the claim's declared obligations. Unscoped claims/actions remain conservatively
   task-wide. `unknown` is not silently upgraded into either support or a typed partial/failure.

4. **Replay owns the revision invariants.** Missing, unreadable, already-superseded, wrong-kind, or
   disjoint targets; irrelevant/success/unknown limitations; non-success support; and incomplete
   limitation sets are rejected by the same pure replay path used by dry-run, memory append, and
   SQLite append. Dry-run therefore proves that the proposed append is structurally effective
   without writing it.

5. **Current evaluation is shared.** Deterministic policies, semantic claim selection, and receipt
   current-claim selection consume one replay-derived effective-claim set. Superseded claims stay
   available to history and historical finding projection. A qualifying recheck can mark findings
   against superseded claims resolved; it does not delete them.

6. **Existing neighboring fields do not change meaning.** `disputes_refs` continues to record an
   explicit unresolved contradiction. `decision_recorded.supersedes_event_id` continues to
   supersede decision history only. Neither is inferred as claim replacement.

7. **Authoring uses existing status contracts.** Candidate finding detail names the accepted v1.1
   field and bounded claim/result ids. `status(history)` supplies event identity and schema version;
   `status(results)` resolves each result id to source event, payload availability, outcome, action,
   and evidence ids. A new status wire version is unnecessary.

## Consequences

A clean prior claim can be replaced after scope grows and produces an honest partial result. The
new completion claim retains that result as a limitation without treating it as support, while an
unrelated obligation's later completion claim does not inherit the limitation. Existing v1 claims
remain readable and keep their historical disclosure behavior.

The stricter v1.1 preflight can reject a proposed correction that an older v1 append would have
accepted and later challenged. This is intentional: the rejection occurs before irreversible
append and gives the author an exact field/invariant plus status views needed to repair it.

## Alternatives considered

**Mutate or delete the old claim.** Rejected because ledger history and receipt provenance are
append-only.

**Reinterpret `disputes_refs` as supersession.** Rejected because it would silently change frozen
v1 semantics and erase the distinction between contradiction and correction.

**Make every failure task-global forever.** Rejected because future and disjoint work cannot have
been disclosed by an earlier claim, and the resulting permanent finding growth is not actionable.

**Add a second status results surface.** Rejected because the existing bounded `results` and
`history` views already expose the structural facts needed to author a correction.

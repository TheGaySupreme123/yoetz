# 03 — Send the reviewer the real frozen, privacy-selected case

**Severity:** critical  
**PR boundary:** semantic case construction, review selection, and privacy candidate composition

**Depends on:** [02](02-one-provider-judgment-contract.md).

## The defect

ADR-006 says the reviewer receives a structured `ReviewPacket`: goal, obligations, claims,
decisions, material timeline, deterministic findings and bases, change observations, coverage,
bounded excerpts, and omissions.

Production sends:

```json
{
  "dependency_digest": "...",
  "findings": [],
  "frontier": {"head_digest": "...", "sequence": 17},
  "schema": "yoetz.semantic-check-candidate/1"
}
```

That object is one `bounded_structural_metadata` item, then base64-wrapped by the privacy envelope.
The rich `SemanticCase`, `ReviewPacket`, `TargetedExcerptRef`, and `ChangeObservation` types are not
constructed by the check path.

The system instruction asks the reviewer to compare facts that are absent and to cite supplied
refs when none are available. In that state, `no_material_discrepancy` with zero challenges is the
only safely expressible result.

## Design

### 1. Build from the frozen authority, never live workspace state

Construct `SemanticCase` and `ReviewPacket` from the frozen check case, pinned deterministic
findings/bases, accepted envelopes, availability facts, and captured objects already authorized at
frontier F.

The builder receives no Git runner, filesystem browser, transcript, environment, database handle,
or provider capability. Missing content is represented as an omission, never fetched or guessed.

### 2. Apply `ReviewContextProfile` before privacy enforcement

Use the configured `ReviewSelectionPolicy`:

- `structural` — typed state, timeline, coverage, IDs, and omissions only;
- `goal_aware` — add goal, obligation, claim, decision, and finding prose;
- `assisted` — add bounded linked evidence/test/failure/diff/source excerpts;
- `expanded|custom` — only broader already-recorded in-scope material.

Selection narrows candidate context; it grants no disclosure authority.

### 3. Preserve categories and item identity

Emit separate case items with their correct `DataCategory`, scope, stable item ID, and link to
case-bound public refs. Do not collapse content into one structural blob. The privacy gateway must
classify, minimize, scan, authorize, and receipt the exact selected items.

The approved payload contains a versioned review-packet schema plus an omission manifest
distinguishing `not_recorded`, `not_selected`, `withheld_by_policy`, and
`redacted_never_send`.

### 4. Make citations reachable and safe

The case allowlist is exactly `frontier_refs ∪ local_check_refs`, digest-bound to the frozen case.
Every ID shown to the reviewer is citable and resolvable. Post-validation maps action/result/
evidence/finding refs back to canonical public subject roots and rejects invented or out-of-case
refs as `semantic_judgment_rejected`, not as an unbounded internal error.

### 5. Bind provenance to what was reviewed

The request commitment and review/case digests bind the exact minimized packet. Keep the stable
system-instruction digest separate and clearly named; do not imply that an instruction-only digest
identifies case content.

## Files

- `src/yoetz/application/check.py`
- `src/yoetz/service/ready_composition.py`
- `src/yoetz/ports/semantic.py`
- privacy selector/enforcer composition
- `docs/INTERFACES.md`, ADR-006/009 only where implementation reveals drift
- deterministic case, privacy, and end-to-end semantic tests

## Tests

- Structural profile sends no prose and declares omitted sections.
- Goal-aware profile includes bounded goal/obligation/claim/decision items with correct categories.
- Assisted profile includes only linked, recorded, capped excerpts.
- Withheld/never-send material is absent and represented by the correct omission reason.
- Every supplied ref belongs to the frozen allowlist; invented refs are rejected boundedly.
- A deterministic finding basis and one captured evidence excerpt can produce a projected semantic
  finding with final provenance.
- Case/frontier/dependency changes invalidate stale output.
- The case builder performs no filesystem, Git, network, transcript, or ambient-state access.
- Privacy receipts bind the exact selected packet and physical dispatch.

## Done

The provider receives the smallest useful case permitted by the selected context profile and
privacy policy, and a challenge can cite real supplied evidence.

## Dogfood observable

The run records the selected packet sections/categories/omissions, obtains at least one
case-bound reviewer challenge, and shows that the challenge changed agent action, evidence, claim,
or explicitly recorded limitation.

## Out of scope

Live repository browsing, provider-driven fetch loops, whole-file disclosure, or a new workflow
operation.


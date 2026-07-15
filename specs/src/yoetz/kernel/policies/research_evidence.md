# src/yoetz/kernel/policies/research_evidence.py — deterministic research-evidence policy pack

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`kernel/projections.md`, `domain/findings.md`, `protocol/coverage.md`, `protocol/errors.md` |
**Imported by:** `kernel/deterministic_checks.md`, `kernel/ranking.md`

## Purpose

This pack evaluates whether the evidence on the page, in the diff, or in the captured state really
supports the claim being made. It is the deterministic policy that catches overconfident research
summaries, unsupported claims, missing limitations, and findings that were rejected without a
credible basis.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `RESEARCH_EVIDENCE_POLICY_ID` | `str = "research-evidence"` |
| `RESEARCH_EVIDENCE_POLICY_VERSION` | `str = "0.1.0"` |
| `RESEARCH_EVIDENCE_POLICY_PACK` | frozen `PolicyPack` instance for this rule set |
| `research_evidence_findings(case)` | run the pack against a frozen case |

## Behavior

The pack examines the frozen case’s claims, evidence refs, subject-state references, and any
captured diff or command metadata preserved in the projection. It emits deterministic findings when
the evidence story and the claim story diverge.

The rule inventory is stable and intentionally small:

- `evidence_does_not_support_claim` — the claim’s supporting refs do not actually justify the
  statement being made.
- `diff_does_not_match_account` — the captured diff or state reference does not match the written
  account.
- `material_limitation_omitted` — the claim or response omits a limitation that the recorded
  evidence makes material.
- `questionable_finding_rejection` — a deterministic finding was rejected or waived without a
  strong enough explanation in the record.

Rule-level behavior is conservative and subject-bound:

- `evidence_does_not_support_claim` triggers only when the supporting refs are actually present
  and they fail to justify the claim being made. It does not trigger when the claim is merely
  shorter than the evidence or when the evidence is irrelevant but harmless.
- `diff_does_not_match_account` triggers only when the captured diff, digest, or described state is
  materially inconsistent with the written account. It does not trigger for minor formatting drift
  or a different but equivalent presentation.
- `material_limitation_omitted` triggers only when the evidence itself makes a limitation material
  to the claim and the claim or response leaves that limitation out. It does not trigger for
  optional caveats.
- `questionable_finding_rejection` triggers only when a deterministic finding was rejected or
  waived without a credible basis recorded in the case. It does not trigger when the rejection is
  accompanied by a matching, bounded explanation and supporting refs.

Like the work-integrity pack, each rule produces at most one finding per logical subject. The pack
normalizes repeated symptoms so the caller sees the strongest record of the mismatch.

`research_evidence_findings(case)` uses only the frozen projection state, the case frontier, the
allowed IDs, and the policy version. It never reads provider output, network resources, or raw
transcripts outside the case. Any evidence comparison is done against the canonical subject-state
and reference metadata already present in the projection.

The pack is conservative about completeness. If the projection lacks enough evidence to prove a
claim wrong, the pack can still emit a limitation finding, but it must not invent a stronger
refutation than the refs justify.

## Errors and edge cases

- Missing or contradictory subject-state references are treated as evidence gaps, not as silent
  success.
- The pack never promotes a weak or partial comparison into a full support claim.
- No provider or storage exception surfaces through this pack.

## Invariants

1. The pack is deterministic and side-effect free.
2. The pack only speaks about research evidence, not generic work integrity.
3. Same frozen case → same finding tuple.
4. The pack never strengthens coverage beyond the available evidence refs.
5. The pack ID and version are stable release identities.

## Tests

- `specs/tests/unit.md` — per-rule fixtures for claim/evidence mismatch, limitation omission, and
  rejected-finding cases.
- `fixtures/policies/research_evidence/` — golden cases and expected findings.

## Open questions

None.

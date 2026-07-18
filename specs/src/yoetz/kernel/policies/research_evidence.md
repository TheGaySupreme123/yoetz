# src/yoetz/kernel/policies/research_evidence.py — deterministic research-evidence policy pack

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`kernel/deterministic_checks.md`, `kernel/projections.md`, `domain/findings.md`,
`protocol/coverage.md`, `protocol/errors.md` |
**Imported by:** `kernel/deterministic_checks.md`, `kernel/ranking.md`

## Purpose

This pack evaluates whether typed evidence links and exact captured-state identities are
structurally consistent with a claim. It is the deterministic policy that catches contradictory
result/state links, omitted typed limitations, and finding rejections without current admissible
support; interpretation of prose belongs only to the semantic path.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `RESEARCH_EVIDENCE_POLICY_ID` | `str = "research-evidence"` |
| `RESEARCH_EVIDENCE_POLICY_VERSION` | `str = "0.1.0"` |
| `RESEARCH_EVIDENCE_POLICY_PACK` | frozen `PolicyPack` instance for this rule set |
| `RESEARCH_EVIDENCE_FACT_CODES` | frozen exact fact-code registry for every rule basis |
| `research_evidence_findings(case: DeterministicCase)` | run the pack and return `tuple[DeterministicAssessment, ...]` |

## Behavior

The pack imports `PolicyPack`, `DeterministicCase`, `FindingFact`, `FindingBasis`, and
`DeterministicAssessment` from `kernel/deterministic_checks.py`. That owning module has no
module-import-time dependency back to this pack; engine dispatch imports this module locally only
after the shared types exist.

The pack examines the deterministic case's claims, evidence refs, subject-state references, and any
captured diff or command metadata preserved in the projection. It emits deterministic assessments
when the evidence story and the claim story diverge, pairing every candidate with a stable
machine-readable `FindingBasis`.

The rule inventory is stable and intentionally small:

- `evidence_does_not_support_claim` — typed supporting refs structurally contradict the claim's
  closed outcome/state fields.
- `diff_does_not_match_account` — comparable captured and claimed state digests differ.
- `material_limitation_omitted` — a completion claim's typed refs omit an exact recorded
  failure/partial/unknown or gap record.
- `questionable_finding_rejection` — a deterministic finding was rejected or waived without
  current structurally admissible evidence refs.

`RESEARCH_EVIDENCE_FACT_CODES` is exactly: `claim_support_present`,
`claim_support_mismatch`, `captured_state_present`, `account_state_mismatch`,
`material_limitation_present`, `limitation_disclosure_absent`, `finding_rejection_present`, and
`rejection_basis_insufficient`. A rule may use only these codes in its observed/missing tuples;
adding or renaming one changes the pack version and its golden basis fixtures.

The rule-to-fact/root crosswalk is exact. `C/X/F/Q` mean a current claim, compared support,
finding, and response-event ID. `X` is an obligation/result/evidence ID for the first two rules and
a single limiting result or gap root for the third. `S(F)` is the responded finding's canonical
public `subject_refs`; `public_root(X)` leaves an obligation/event/claim unchanged and maps a
result/evidence to its current source event. Each listed tuple is sorted unique. Missing-column
codes enter `required_but_missing_facts`; all others enter `observed_facts`; supporting refs are the
exact union of observed-fact refs.

| Finding kind | One raw trigger and exact candidate `subject_refs` | Exact observed fact refs | Exact missing fact refs |
|---|---|---|---|
| `evidence_does_not_support_claim` | one structurally mismatching pair `(C,X)` -> `(C,public_root(X))` | `claim_support_present:(C,X)`; `claim_support_mismatch:(C,X)` | none |
| `diff_does_not_match_account` | one comparable state pair `(C,X)` -> `(C,public_root(X))` | `captured_state_present:(C,X)`; `account_state_mismatch:(C,X)` | none |
| `material_limitation_omitted` | one completion-claim/limitation pair `(C,X)` -> `(C,public_root(X))`; a rootless material gap uses `(C)` -> `(C)` | `material_limitation_present:(C,X)` or `(C)` for a rootless gap | `limitation_disclosure_absent` uses the identical tuple |
| `questionable_finding_rejection` | primary `(F,Q)` -> `S(F)` | `finding_rejection_present:(F,Q,<sorted response evidence refs>)` | `rejection_basis_insufficient:(F,Q)` |

An unavailable or unresolved `X` does not fabricate a root: it routes to the work-integrity
admissibility rule or a typed case gap as described below. An unreadable responded finding cannot
supply `S(F)` and therefore yields only a coverage limitation. The comparison inputs are only typed
IDs, closed enums, exact digests/frontiers, availability facts, and ref presence. No claim, account,
limitation, or response prose is parsed. Candidate priority and actionability come only from
`FINDING_KIND_TRAITS` in `specs/INTERFACES.md`.

Rule-level behavior is conservative and subject-bound:

- `evidence_does_not_support_claim` triggers only when the supporting refs are actually present
  but structurally contradict the typed claim state: a completion claim references a
  `failure|partial|unknown` result, asserts an obligation that is still projected open, or carries
  an exact comparable subject-state digest that differs from its support. Empty, unresolved,
  redacted, or merely weak support routes to `claim_without_admissible_evidence` or a coverage
  limitation instead. This rule never interprets whether evidence prose justifies claim prose.
- `diff_does_not_match_account` triggers only when the captured structural state is
  structurally comparable and its exact `diff_digest` or `tree_digest` differs from the claim's
  corresponding digest. `described_state` and written-account prose are explanatory and ignored;
  incomparable formats produce a coverage limitation instead of this mismatch kind.
- `material_limitation_omitted` triggers only when the evidence itself makes a limitation material
  through a closed structural fact (a linked `failure|partial|unknown` result, a redaction marker,
  or a stale/incomparable state gap) and the completion claim's typed support refs omit the exact
  limiting record. It never searches the statement for caveat language.
- `questionable_finding_rejection` triggers only when a deterministic finding was rejected or
  waived without current, structurally admissible `evidence_refs` at the exact finding frontier.
  It never grades the required reason string and does not trigger when matching typed support is
  present.

Policy cardinality follows the engine contract exactly. For each rule, the logical subject key is
the candidate's complete canonical `subject_refs` tuple. The pack groups every raw structural
trigger input for that exact tuple before rule evaluation, evaluates the grouped input exactly
once, and emits zero or one assessment for the emitted-key identity
`(RESEARCH_EVIDENCE_POLICY_ID, rule_id, subject_refs)`. Repeated raw triggers are therefore inputs
to one evaluation. If pack wiring nevertheless emits the same key twice, the engine rejects the
duplicate as a policy-wiring defect; no duplicate-assessment reconciliation path exists. Different
research-evidence rules may still emit separate assessments for the same subject tuple because
their `rule_id` members differ.

The four kind tokens in this pack describe evidence problems, not semantic origin. This pack emits
them with `origin=deterministic`; a model may independently propose the same kind with
`origin=semantic_model_derived`. Origin/provenance, not kind, is authoritative.

Each rule basis names the precise claim/evidence/diff/response refs it observed, the support fact
that failed or was missing, the subject-state relation when applicable, visibility of any linked
excerpt, and its coverage gaps. When content is absent, the rule may emit an evidence limitation but
cannot claim it inspected or refuted the missing content.

`research_evidence_findings(case)` uses only the frozen projection state, the case frontier, the
allowed IDs, and the policy version. It never reads provider output, network resources, or raw
transcripts outside the case. Any evidence comparison is done against the canonical subject-state
and reference metadata already present in the projection.

The pack is conservative about completeness. If the projection lacks enough evidence to prove a
claim wrong, the pack can still emit a limitation finding, but it must not invent a stronger
structural mismatch than the refs justify.

Every candidate's coverage uses the same exact derived-finding transformation as the
work-integrity pack. The pack first folds `coverage.weakest` over
`case.coverage_by_ref[ref]` for every ref in the assessment's exact sorted
`FindingBasis.supporting_refs`; the tuple is nonempty for every emitted finding, and any missing
entry makes the case malformed. It then preserves the folded `authorship_assurance`,
`artifact_observation`, `evidence_immutability`, `ledger_freshness`, and `known_gaps` byte-for-byte,
adds `engine_derived` to the sorted-unique publication channels, and adds `deterministic` to the
check types while removing `none`. It never applies the `engine_derived` channel default to the
ordered dimensions and never strengthens or otherwise rewrites them. The candidate's
`subject_frontier` is exactly `case.frontier`.

## Errors and edge cases

- Missing or contradictory subject-state references are treated as evidence gaps, not as silent
  success.
- The pack never promotes a weak or partial comparison into a full support claim.
- No provider or storage exception surfaces through this pack.

## Invariants

1. The pack is deterministic and side-effect free.
2. The pack only speaks about research evidence, not generic work integrity.
3. Same frozen case → same assessment tuple.
4. The pack never strengthens coverage beyond the available evidence refs.
5. The pack ID and version are stable release identities.
6. Evidence problem kind never implies semantic provenance; the origin field remains authoritative.
7. All raw triggers for one rule and complete subject tuple are evaluated once, and duplicate
   emitted keys are rejected rather than reconciled.

## Tests

- `specs/tests/unit/kernel/test_policy_research_evidence.py.md` — inline exact trigger and closest
  non-trigger for every rule.
- `specs/fixtures/README.md` maps the finite public research-evidence vectors to exact case files
  `ADV-002-omitted-failed-test`, `ADV-004-irrelevant-evidence`, and
  `ADV-009-wrong-semantic-finding-rejected`; no separate research-evidence fixture-resource
  directory exists.

## Open questions

None.

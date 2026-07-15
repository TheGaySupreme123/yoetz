# tests/property/test_egress_policy_properties.py — generated privacy monotonicity and leak fences

**Wave:** B/C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
domain/application specs and privacy fixtures | **Imported by:** property suite and release privacy
gate

## Purpose

Prove policy composition, minimization, authorization and receipt behavior remain safe across input
order, arbitrary overlay chains, scope trees, content mixes and retry interleavings.

## Public surface

Properties generate valid policies/proposals and adversarial near-valid values for all enums,
four privacy profiles, five review-context profiles, channels, scopes, categories/classes/forbidden
kinds, local sinks, caps, transformations, authorizations and outcomes using deterministic
Hypothesis strategies.

## Behavior

Assert intersections are associative/commutative/idempotent where ordering is irrelevant and never
increase permission; a verified widening changes only exact confirmed dimensions; tightening is
immediate. Any forbidden kind, unknown classification or invalid scope always blocks. Candidate
permutation yields identical minimized canonical case. Redaction/minimization cannot increase bytes,
categories, scope or sensitivity. Authorization changes to case/policy/destination/purpose/scope/
generation/expiry invalidate it. Generated global-ceiling/channel matrices prove a false ceiling
accepts only the all-disabled vector and a true ceiling grants nothing. For the four v0.1 non-LLM
rows, proposed enablement is `channel_unavailable`, stores no dormant consent, and makes no I/O;
forced enabled states yield only no-dispatch decision receipts. Generated local-model cases satisfy
the same fences and cause no external action.

Receipt generation is checked for plaintext/canary absence, exact structural counts, keyed
commitment only for a physical attempt, exact audit-store/algorithm/body-count conditionals, and
support for taskless structural channels. Generated early blocks use `PreDispatchAuditDecision` and
can never yield a `DisclosureProposal`/authorization. Generated pending/approved/receipt-repair
states cannot validate as terminal receipt outcomes; `dispatched` and `key_slot_ref` are always
rejected. Generated terminal receipts prove outcome/reason compatibility is total: success has no
failure reason, every failure has exactly one allowed reason, and arbitrary cross-pairs never
validate for either network or local-disclosure receipts. Agent-context and trusted-
human-control outputs are generated as local disclosures and never misclassified as network egress.

Generated context selections prove `structural ≤ goal_aware ≤ assisted ≤ expanded` only with
respect to eligible already recorded candidates; `custom` follows its explicit selector. Selection
never grants category/class/scope/provider/channel authority, returns no ambient handle, and emits a
typed omission for every relevant candidate it cannot include. Subject-state relation and content
visibility remain independent across generated claimed-change combinations. The generator varies
both `include_finding_prose` and `include_exact_command_text`; their meet is logical AND, and neither
can bypass its category/class fence.

## Errors and edge cases

Shrinkers retain the security-triggering item/scope transition. Tests cap bytes before expensive
scan/canonicalization and include UTF-8 boundaries, normalization distinctions, encoding splits,
duplicate/sorted sets, max/max+one, expiry equality and cancellation/dispatch races.

## Invariants

1. Effective permission never grows without exact verified local-human widening.
2. Never-send non-overridability holds for every generated profile/sink/channel.
3. Input order never changes disclosure bytes or decision.
4. Independent channels remain independent under arbitrary subsets.
5. Structural receipts cannot contain generated candidate substrings.
6. A future capability-set change cannot transform a prior unavailable proposal into active
   consent; a fresh verified local-human widening is required.
7. Initial reservation failure is no-receipt and pre-dispatch; every reserved terminal decision and
   every physical attempt has exactly one valid terminal receipt.
8. Review-context selection may only narrow recorded candidate material; disclosure policy may
   narrow it again but neither layer can widen the other.

## Tests

Run `uv run --locked pytest tests/property/test_egress_policy_properties.py -q`; CI persists seed and
minimal counterexample as public synthetic data only.

## Open questions

None.

# tests/unit/privacy/test_policy_and_contracts.py — privacy values, schemas, setup and mutation rules

**Wave:** B/C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** domain
privacy, privacy-policy application, four privacy schemas, setup protocol | **Imported by:** unit
suite, privacy critical-path coverage gate

## Purpose

Lock every privacy enum, closed schema branch, cross-field profile rule, policy diff, authorization
binding, and plaintext rejection without network, filesystem, database, keyring, or wall clock.

## Public surface

Named tests cover every `PrivacyProfile`, `ReviewContextProfile`, `EgressChannel`,
`LocalDisclosureSink`, `DataClass`,
`DataCategory`, `ForbiddenDataKind`, scope/consent/outcome enum; four valid policy profiles; every
invalid branch; setup union messages; request/receipt shapes; tightening/loosening classification;
provider data-use records, transparent recipe expansion; and exact reason-code mapping.

## Behavior

Use frozen explicit UUIDv4 IDs, times, digests and policies. Assert all five channel policies are
required/independent and `network_egress_permitted=false` requires them all disabled. A true ceiling
grants nothing. `local_only` forbids external LLM construction/user-content egress but is not a
non-LLM channel decision; confirm-every-request permits an explicitly policy-allowed sensitive
excerpt only after exact preview; minimal-external excludes sensitive/confidential;
trusted-provider remains category/provider/purpose/scope bounded. Test every never-send kind is
unrepresentable as approved content and all three local sinks use the same fence.
`ReviewContextProfile` is an orthogonal selection ceiling: its exact selector includes separate
finding-prose and exact-command booleans, and it cannot authorize a category, class,
scope, provider, or channel. The installation seed is exactly `local_only + structural + network
false + all channels off`; it is distinct from the editable upstream `assisted_review` recipe.

Validate source schemas against positive/negative objects, including taskless egress receipt,
request commitment branches, no-null absence, 16/256 KiB boundaries, sorted sets, canonical ID/time/
digest spelling, canonical review-packet indexing, paired-or-absent deterministic summary/detail
refs, structural no-prose enforcement, and forbidden secret/free-text fields. Setup tests expand every recipe into all
thirteen typed answers and prove the exact assisted recipe categories/classes/context/scope/preview
values. A current exact endpoint data-use record with training `prohibited`, retention
`none|bounded`, and provider human access `prohibited|restricted` makes that recipe eligible;
`permitted|unbounded|unknown`, stale, or mismatched evidence removes the recommendation without
granting or revoking a user-authored custom policy. Setup tests
also prove MCP/agent cannot construct
confirmation, stale draft/revision/expiry commits nothing, tightening can commit, and widening
requires authenticated local-human authority. The four v0.1 non-LLM channel answers render
unsupported/off; an attempted enabling transition returns `channel_unavailable` without storing
dormant consent. Forced enabled input yields a pre-dispatch outcome/reason `channel_unavailable/channel_unavailable`
decision receipt that forbids authorization/dispatch/commitment/attempt-body fields. Capability
reconciliation never activates old intent; future support requires a new exact human transition.
For `confirm_every_request`, prove an unconsumed authorization resumes the same dispatch after
crash, while every consumed-attempt retry requires a new proposal, foreground decision,
authorization, dispatch, and receipt; automatic profiles alone may retry from baseline policy.
Freeze `PrivacyAuditSubject = PreDispatchAuditDecision | AgentProjectionAuditSubject |
DisclosureProposal`: early policy, classification, never-send, and unsupported-channel blocks
reserve only the pre-dispatch structural branch; agent projection stores only keyed commitments/
field decisions and no object; only disclosure proposal can construct/authorize a prepared provider
case. `awaiting_human`, `approved`, and `receipt_pending` are
audit states rejected by the terminal `PrivacyOutcome`/receipt schema. Initial reserve failure yields
bounded no-ID/no-receipt `audit_failed`; a later failure on an existing reservation may be receipted
after recovery, while post-consumption repair retains the real attempt outcome.

Receipt schema vectors require `audit_store_version=1`, exact algorithm
`hmac-sha256/yoetz-privacy-egress-request-v1`, canonical prefixed lowercase-hex commitment, and
`counts.request_body_bytes` exactly when `dispatch_id` exists. They reject `dispatched`,
`key_slot_ref`, missing/extra attempt fields, and implementation-selected algorithm/version values.
They enumerate the complete egress/local-disclosure outcome-reason matrix: `completed` forbids
`safe_failure_reason`, every failure requires one, and every cross-pair is rejected.

## Errors and edge cases

Hit every unknown field/enum, duplicate set, mismatch, overflow, stale generation, reused
authorization, path-like scope, redirect, raw exception, plaintext receipt and hidden-secret field.
Failure diagnostics contain only safe pointer/reason, never rejected values.

## Invariants

1. Pure tests are deterministic and offline.
2. Every frozen vocabulary member has positive and rejection coverage.
3. No valid object can override never-send or infer one channel from another.
4. Schema and domain model accept/reject sets agree.
5. Policy widening cannot be mislabeled neutral/tightening.
6. `channel_unavailable` is distinct from an attempted/ambiguous transport and has no attempt-only
   receipt fields.
7. No one-dispatch human decision can authorize two physical provider attempts.
8. A finished receipt is terminal; waiting/approval/repair remains only in audit state.
9. Initial reservation failure cannot fabricate durable audit evidence or hide a physical attempt.
10. Recommendation metadata is inspectable evidence, never policy authority or a technical proof
    of provider behavior.

## Tests

Run as `uv run --locked pytest tests/unit/privacy/test_policy_and_contracts.py -q`; branch coverage
for policy evaluation, mutation classification, authorization validation and schema adapters is 100%.

## Open questions

None.

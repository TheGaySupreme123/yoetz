# tests/conformance/privacy/test_privacy_profiles.py — four-profile and setup conformance matrix

**Wave:** C/E/F | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
schemas/docs, CLI/MCP/service surfaces, PRIV-001..004 | **Imported by:** conformance and public-claim
release gates

## Purpose

Prove all public/control surfaces and adapters implement the same exact four privacy profiles,
preview/setup contract and honest semantic-degradation result.

## Public surface

Parameterized cases run `PRIV-001`, `PRIV-002`, `PRIV-003`, and `PRIV-004` application-direct,
trusted local CLI control, ordinary CLI, MCP bridge, local-model fake and approved external fake.

## Behavior

Assert canonical policy/view/result equality after transport envelopes. Local-only constructs no
external LLM adapter and discloses no task/user content externally; local model uses the same
classification/minimization/never-send fences. The zero-network PRIV-001 variants additionally bind
`network_egress_permitted=false` and all five channel policies disabled. PRIV-008 separately proves
that `local_only` does not decide non-LLM consent: v0.1 marks each of those four choices unavailable,
rejects its enabling transition, and makes no I/O. A future owned channel could coexist only after a
fresh exact local-human transition. Confirm-every-request exposes bounded preview only to trusted
human control, rejects MCP/agent approval, resumes the same unconsumed dispatch without re-prompting,
and requires a fresh preview/decision for every later physical retry. Waiting and approval are
nonterminal audit states with no finished egress receipt; only denial/expiry/pre-dispatch failure or
a terminal physical-attempt outcome is receipted. Minimal external freezes exact removals/redactions.
Trusted-provider remains exact binding/category/purpose/scope, including bounded sensitive content.

The setup matrix asks all ten questions, shows allowed/blocked examples, defaults the global ceiling
false and all five channels denied, keeps the ceiling and five consents separate, and classifies
each diff. `network_egress=true` enables nothing; false plus any enabled channel is rejected.
Ordinary CLI/MCP may inspect safe policy status but cannot confirm widening. Provider
refusal/timeout/invalid yields explicit incomplete semantic coverage and the same deterministic
final result across surfaces.

## Errors and edge cases

Unknown profiles, unsupported provider/local runtime, stale policy, changed preview bytes, expired
approval, wrong surface assurance, redirect and missing receipt are identical bounded errors. Human
rendering cannot omit material blocked categories or call incomplete semantic work verified.

## Invariants

1. Four profiles have one LLM-disclosure meaning on every surface and imply nothing about non-LLM
   channel consent.
2. Setup/rendering cannot widen Core policy.
3. Core gives local and external models different transport authority but identical disclosure
   fences; a separate local runtime's ambient authority is an explicit F-013 limitation.
4. MCP/agent protocol input cannot create local-human policy authority or substitute for the
   confidential foreground decision path; F-011 records the malicious same-UID limit of TTY-only
   per-request consent.
5. Deterministic results survive semantic incompleteness.
6. Zero-network evidence binds the false global ceiling and all disabled channels rather than the
   `local_only` token alone.
7. v0.1 exposes no dormant non-LLM consent or transport; later capability availability is a fresh
   widening.
8. `confirm_every_request` never hides multiple physical attempts behind one foreground decision.
9. Public surfaces never serialize `awaiting_human|approved|receipt_pending` as terminal
   `PrivacyOutcome` values.

## Tests

Run `uv run --locked pytest tests/conformance/privacy/test_privacy_profiles.py -q` against memory and
durable backends.

## Open questions

None.

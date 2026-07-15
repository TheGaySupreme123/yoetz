# tests/conformance/privacy/test_privacy_profiles.py — four-profile and setup conformance matrix

**Wave:** C/E/F | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
schemas/docs, CLI/MCP/service surfaces, PRIV-001..004 | **Imported by:** conformance and public-claim
release gates

## Purpose

Prove all public/control surfaces and adapters implement the same exact four privacy profiles, five
review-context profiles, preview/setup contract, recommendation eligibility, and honest
semantic-degradation result.

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
Every context profile is independently intersected with those disclosure rules. Assisted review may
include only linked problem-local recorded excerpts and must emit exact omission reasons; expanded
or custom still cannot fetch the repository or override never-send.

The setup matrix asks all thirteen questions, shows allowed/blocked examples, starts from the
fail-safe `local_only + structural` zero-egress seed, and separately displays the editable
`assisted_review` recommendation. That recipe expands to the exact standing workspace trusted-
provider policy, assisted context, named ordinary categories, sensitive/confidential and transcripts
off, and no per-request preview. It appears only for a current exact endpoint data-use record whose
training posture is `prohibited`, retention is `none|bounded`, and provider human access is
`prohibited|restricted`. `permitted|unbounded|unknown`, stale, or mismatched evidence removes the
badge; it does not prevent a local human from authoring a custom policy. The matrix keeps the
ceiling and five consents separate and classifies each diff.
`network_egress=true` enables nothing; false plus any enabled channel is rejected.
Ordinary CLI/MCP may inspect safe policy status but cannot confirm widening. Provider
refusal/timeout/invalid yields explicit incomplete semantic coverage and the same deterministic
final result across surfaces.
After a standing assisted policy is committed, normal checks, bounded retries, reviewer findings,
agent responses, and rechecks do not await a human. Policy widening, credential mutation,
`confirm_every_request`, and finding waiver retain their explicit human authority paths.

## Errors and edge cases

Unknown profiles, unsupported provider/local runtime, stale policy, changed preview bytes, expired
approval, wrong surface assurance, redirect and missing receipt are identical bounded errors. Human
rendering cannot omit material blocked categories or call incomplete semantic work verified.

## Invariants

1. Four profiles have one LLM-disclosure meaning on every surface and imply nothing about non-LLM
   channel consent.
2. Setup/rendering cannot widen Yoetz policy.
3. Yoetz gives local and external models different transport authority but identical disclosure
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
10. The safe installation seed and the user-visible assisted recommendation are different, and
    neither silently commits authority.

## Tests

Run `uv run --locked pytest tests/conformance/privacy/test_privacy_profiles.py -q` against memory and
durable backends.

## Open questions

None.

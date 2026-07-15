# src/yoetz/adapters/privacy/local_enforcer.py — deterministic classifier, minimizer, and scanner

**Wave:** C–D | **ADRs:** ADR-009 | **Imports (spec-tree):** `domain/privacy.md`,
`ports/privacy.md`, `observability/privacy.md`, `protocol/canonical.md` | **Imported by:**
runtime composition and privacy tests

## Purpose

Implement `PrivacyClassifierPort` locally with reviewed source/category rules, scope validation,
deterministic minimization/redaction, and an exact-byte secret scan. It is the content fence shared
by external providers, local models, and agent-context projection.

## Public surface

- `class LocalPrivacyEnforcer(PrivacyClassifierPort)`.
- `class ClassificationRuleset` — immutable versioned category/source/scope rules.
- `class ReviewSelectionRuleset` — immutable profile-specific timeline/relevance/excerpt ordering.
- `class MinimizationRuleset` — immutable purpose-specific field ordering and byte/token ceilings.
- `class SecretScanRuleset` — immutable detector-version identity.

## Behavior

`classify` requires every item to have a recognized source label, declared category, and proof that
its origin is inside the selected scope. It applies the most sensitive matching class; disagreement
or missing proof is `classification_uncertain`. Source rules mark structural never-send sources
(vault/keyring/credential files, environment, raw database/log/transcript access, out-of-scope
files) before byte scanning.

`minimize_and_scan` removes nonrequired categories and lowest-priority items first, applies only
versioned deterministic redactions, serializes the exact approved logical case bytes, and scans
those bytes. It cannot claim to scan provider framing or headers that do not exist yet. The
provider renderer/gateway later performs a second scan of the exact final application request body
and verifies it contains only the approved logical case plus fixed reviewed template/schema fields;
authentication metadata, HTTP framing, and TLS are outside both content scans. A forbidden source
or scan finding at either stage blocks before network I/O with kind/count only. There is no model
classifier, arbitrary regex from config, ignore flag, or provider-specific enrichment.

For semantic cases, `ReviewSelectionRuleset` runs before classification and is deterministic.
`structural` selects only typed facts; `goal_aware` adds allowed intent/claim prose; `assisted`
mechanically follows claim/obligation/deterministic-finding/action/result/evidence refs and ranks
linked failing-test/command excerpts, changed hunks, enclosing-symbol context, and directly
supporting/contradicting evidence. It does not read Git/filesystem or accept a provider context
request. `expanded|custom` may select a broader recorded set under their exact policy. Unselected or
unavailable subjects receive typed omission entries; exact command strings and unrelated adjacent
source lose to problem-local material under the upstream assisted ordering.

The ruleset consumes the effective compiled `ReviewSelectionPolicy`, never only its display enum.
It filters sections and excerpt kinds, enforces linked-only versus linked-then-in-scope relevance,
excludes finding prose or exact command text unless the corresponding selector boolean and
category policy both allow it, then applies all
selector and case caps. Assisted excerpt priority is fixed: linked failing-test/failure, directly
contradicting evidence, changed hunk, enclosing symbol, directly supporting evidence, material
command metadata, then other linked repository context. Ties sort by material subject-ref bytes,
source-state digest bytes, occurred order, source-ref bytes, then item ID. Timeline facts sort by
occurred order then event/action/result/evidence ID. Same candidate + effective selector therefore
produces the same item-ID index and omission order on every adapter.

Before privacy evaluation, omissions may be only `not_recorded|not_selected` or a pre-existing
structural `redacted_never_send` marker that carries no forbidden bytes. Policy intersection may add
`withheld_by_policy`. A newly detected forbidden source/scan match blocks the case and never becomes
a provider-visible omission.

## Errors and edge cases

Oversized input is processed locally but cannot yield an oversized case. Invalid encoding is
handled as bytes. Scanner failure and unknown ruleset version fail closed. Matches never appear in
errors or receipts.

## Invariants

1. Same candidate, policy, and rulesets produce identical classification and bytes.
2. Approved logical case bytes receive the local scan, and the gateway scans the exact final
   application body after deterministic rendering; no earlier approximation is called final.
3. Never-send findings cannot be redacted into permission; the source item remains blocked.
4. The adapter performs no filesystem discovery or network I/O.
5. Selection never confuses `not_recorded|not_selected|withheld_by_policy|redacted_never_send` with
   observed same-state content.

## Tests

Golden classification/minimization fixtures, every forbidden source/pattern, scope confusion,
chunk boundaries, cap edges, byte-for-byte determinism, and renderer-injected final-body canaries
that the gateway must block before authorization consumption/network I/O are covered. Review
selection fixtures cover every context profile, linked-versus-unrelated ordering, excerpt caps, and
the omission/change-visibility matrix.

## Open questions

None.

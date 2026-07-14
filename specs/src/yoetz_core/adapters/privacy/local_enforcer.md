# src/yoetz_core/adapters/privacy/local_enforcer.py — deterministic classifier, minimizer, and scanner

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

## Tests

Golden classification/minimization fixtures, every forbidden source/pattern, scope confusion,
chunk boundaries, cap edges, byte-for-byte determinism, and renderer-injected final-body canaries
that the gateway must block before authorization consumption/network I/O are covered.

## Open questions

None.

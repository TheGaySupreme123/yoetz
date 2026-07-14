# src/yoetz_core/config/privacy.py — first-run privacy seed and policy-bootstrap validation

**Wave:** C | **ADRs:** ADR-008, ADR-009 | **Imports (spec-tree):** `domain/privacy.md` |
**Imported by:** `config/models.md`, `application/privacy_policy.md`, runtime composition

## Purpose

Define the safe one-time seed used only when no durable privacy policy exists, without turning
config files, environment variables, or CLI process arguments into continuing disclosure
authority. Mutable scoped policy and human decisions live behind `PrivacyPolicyStorePort`.

## Public surface

- `class PrivacyBootstrapConfig` — frozen strict model containing the initial machine policy
  profile, global `network_egress_permitted` ceiling, initial local-model choice, and five explicit
  initial channel choices.
- `safe_privacy_bootstrap() -> PrivacyBootstrapConfig` — `local_only`,
  `network_egress_permitted=false`, all five network channels denied, local model disabled.
- `seed_policy_if_absent(config, store) -> PrivacyPolicy` — atomically persists generation 1 only
  when the policy store proves absence; otherwise returns the existing policy unchanged.

## Behavior

The only accepted v0.1 bootstrap value is the reviewed safe seed: `local_only`,
`network_egress_permitted=false`, all five network channels denied, and local model disabled. A
missing section yields the same value. On first ready startup, the policy store atomically persists
that seed as the machine policy generation 1. Once any durable policy exists, bootstrap config has
no intersection/ceiling effect and cannot overwrite, tighten, widen, reset, or roll back it. The
word “ceiling” after bootstrap refers to the durable policy field, never this config seed. All later
changes go through
`application/privacy_policy.md`; widening requires trusted local-human reauthentication and
tightening uses the same service-owned store.

Provider/model/endpoint profile IDs are nonsecret structural references. External and local
provider credentials, socket handles, raw URLs, headers, tokens, and keyring locators are forbidden.
The unlocked service resolves exact profile IDs to trusted destinations and opaque handles.

Each of the five initial channel choices is explicit and independent beneath the global ceiling.
The global ceiling being true would authorize no channel, while false requires all five denied.
Enabling LLM inference in stored policy has no effect on telemetry, diagnostics, updates, or
capability testing. `PrivacyProfile` controls LLM disclosure only; a later durable `local_only`
policy may validly enable one bounded structural non-LLM channel when its global ceiling is true.
Local-model permission is separate and does not imply external inference permission.

## Errors and edge cases

Unknown/non-local profile, `network_egress_permitted=true`, enabled initial channel, enabled initial
local model, omitted channel row, generic endpoint URL, secret-like key, or credential value fails
closed with a bounded `ConfigError`. A crash during first seed is resolved by the policy store's
absent-or-generation-1 transaction; config never replaces an existing policy.

## Invariants

1. Built-in and missing-config defaults set the global ceiling false and permit no network egress.
2. Configuration seeds one denied policy only; after generation 1 it is not policy authority.
3. Credentials and confidential handles have no config representation.
4. The global network ceiling, all five channel decisions, and local-model permission remain
   explicit; the ceiling grants nothing and channel decisions remain mutually independent.
5. Validation is pure and performs no I/O.

## Tests

Defaults, strict parsing, global-ceiling/five-channel completeness, rejected permissive seeds, concurrent
absent-or-seed, restart/no-overwrite, and secret/generic-endpoint rejection are covered.

## Open questions

None.

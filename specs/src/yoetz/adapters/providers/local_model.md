# src/yoetz/adapters/providers/local_model.py — exact AF_UNIX local semantic-model adapter

**Wave:** E | **ADRs:** ADR-006, ADR-008, ADR-009 | **Imports (spec-tree):**
`domain/privacy.md`, `ports/semantic.md`, `config/models.md` | **Imported by:** privacy gateway
composition and local model capability tests

## Purpose

Provide optional local semantic inference through one service-approved, owner-only AF_UNIX
endpoint profile. The Yoetz adapter itself opens no network transport and receives only an
`ApprovedLocalDisclosureCase`; local execution does not weaken classification, minimization, secret scan,
post-validation, or audit requirements.

## Public surface

- `class LocalModelEvaluator(SemanticEvaluatorPort)`.
- `class InstalledLocalModelProfileRegistry` — immutable artifact-owned allowlist that resolves an
  exact `LocalModelProfileConfig` tuple or returns bounded unavailable; it has no dynamic discovery
  or user extension mapping.
- `class LocalModelEndpointProfile` — exact profile ID/version, model ID, protocol/schema versions,
  expected endpoint identity/owner/peer properties, timeout, release-resource digest, and capability
  evidence digest; no generic URL/path string.
- `class LocalModelSocketResolverPort(Protocol)` — service-internal platform resolver with
  `resolve(profile) -> LocalModelSocketHandle`; only the trusted daemon composition implements it.
- `class LocalModelSocketHandle` — nonserializable, generation-bound, one-profile AF_UNIX connected
  handle; it exposes bounded send/receive operations, not its filesystem path or raw socket.
- `normalize_local_response(...) -> SemanticResult`.

## Behavior

The packaged module/resource owns a closed registry of exact release-supported tuples. Each entry
binds profile and endpoint-profile versions, model, protocol, judgment schema, timeout ceiling,
expected local service identity/owner/peer/mode rules, packaged resource digest, and exact
capability-evidence digest. A release with no passing exact local-runtime cell ships the registry
empty and reports local semantic inference unavailable; merely having this adapter contract does
not advertise support.

At ready composition, configuration may identify an installed tuple but performs no resolve,
socket open, peer probe, or evaluator construction. Only `reconcile_policy`, after the effective
durable policy proves `local_model_enabled`, the exact binding, allowed categories/purpose/scope,
and current service generation, may ask the trusted platform resolver for a socket handle. The
resolver alone maps that entry to
the platform-owned endpoint locator, opens AF_UNIX with no-follow/replacement protections, verifies
owner, mode, socket type, peer credentials and expected runtime identity, and returns a
nonserializable handle bound to service generation and profile digest. Neither config nor privacy
policy supplies the path. A mismatch, missing resolver, or unsupported platform leaves the adapter
absent with structural unavailability.

The resolver verification and `LocalModelEvaluator` construction occur within the same generation-
fenced reconciliation candidate; a policy/generation change closes the candidate before registry
publication. Disabled policy leaves zero socket attempts. The adapter performs no
path discovery, DNS, AF_INET/AF_INET6, proxy lookup, redirect, subprocess launch, package/model
download, or fallback. It sends one bounded approved case and parses the exact structured judgment
schema. The bundled adapter is trusted service code and receives no repository/storage/environment
handles through composition; v0.1 does not claim in-process sandbox isolation from ambient OS
authority.

The pre-existing model runtime is a separate trusted local disclosure sink. AF_UNIX proves only how
Yoetz delivered the case, not that the runtime process lacks AF_INET or cannot exfiltrate. A support
cell may claim runtime no-network isolation only when it binds verifiable sandbox/network-denial
evidence to that exact runtime artifact/profile. Without it, public UI/docs state that approved
content is disclosed to the named local runtime and do not claim its later network behavior.

The local adapter consumes the same structured `ReviewPacket` and returns the same
`ReviewerChallenge` schema as an external adapter. The effective `ReviewContextProfile` controls
selection, and the local-model category ceiling remains independent. Problem-local source means a
bounded excerpt already recorded in the frozen case; the adapter receives no workspace handle and
cannot ask the service or model runtime to fetch more. Missing/withheld content remains an explicit
omission and cannot be interpreted as unchanged code.

For each physical call, the gateway atomically `consume_local`s the fresh proposal immediately
before the first AF_UNIX write. A consumed proposal is never resent on crash/replay; completion
records the actual or `outcome_unknown` `LocalDisclosureReceipt`, and retry uses a fresh proposal.
The semantic attempt retains local
model/endpoint provenance. Output is untrusted `semantic_model_derived` and follows the same strict
post-validation as external output. Absence/unavailability never silently selects an external
provider.

## Errors and edge cases

Missing installed tuple/socket/resolver/evidence, peer or generation mismatch, unsupported schema/
model, timeout, refusal, invalid/truncated output, or socket replacement returns bounded semantic
status with no raw response retention. A socket path or launch/download instruction from normal
config, privacy policy, CLI/MCP, environment, or model output is rejected.

## Invariants

1. The Yoetz adapter creates no network socket and performs no destination discovery; this does not
   attest the separate runtime's network behavior.
2. Only a service-approved AF_UNIX handle and exact capability profile are accepted.
3. Input is an exact approved local-disclosure case; network outbound cases, credentials, and
   never-send material remain inaccessible.
4. Local model output cannot strengthen deterministic coverage without post-validation.
5. External fallback is impossible.
6. Configuration can select only an exact installed tuple; it cannot choose or discover transport.
7. No advertised support cell exists without artifact-bound profile and capability evidence.
8. Config selection alone creates no socket/IPC capability; policy reconciliation and local consume
   gate construction and each physical write.
9. Local/external reviewers use the same basis/challenge/change-visibility semantics even though
   their transport and data-use recommendation rules differ.

## Tests

Empty/uninstalled/mismatched registry, every forbidden config locator, exact profile success,
peer/socket replacement, permissions/generation checks, schema/refusal/timeout/invalid matrix,
AF_INET/AF_INET6/DNS/proxy/subprocess/download denial in the Yoetz adapter, honest runtime trust
wording, no external fallback, and local disclosure receipt tests are required before advertising a
local-model support cell. Runtime-wide no-network wording additionally requires exact sandbox
evidence; adapter-only socket interception is insufficient.

## Open questions

None.

v0.1 includes this adapter contract, but a release advertises local semantic support only for
an exact endpoint/model profile with artifact-bound capability evidence.

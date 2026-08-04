# Semantic dogfood runbook

This runbook governs sessions that use Yoetz on Yoetz and then report a finding about it. Its
purpose is to fix, **before** the first task action, which claim the run is allowed to make — so a
result is never read as evidence for a question the run's configuration made unanswerable.

It exists because of the 2026-08-03 postmortem
([`docs/postmortems/2026-08-03-codex-testing-yoetz-schema-feedback-influence.md`](../postmortems/2026-08-03-codex-testing-yoetz-schema-feedback-influence.md)),
which scored "did semantic feedback help?" against a session whose MCP route was `strict`. Yoetz
behaved correctly and reported honestly — `semantic_status=blocked_by_policy`,
`semantic_reason=route_semantic_ceiling`, `semantic_provenance: null`, zero egress. The run simply
could never have measured semantic usefulness. Nothing in the tooling said so in advance.

Three facts this runbook keeps apart:

- **Registration is not activation.** A registered MCP entry says what Codex would launch, not that
  it launched or that a model was shown anything.
- **Availability is not usefulness.** A route that *can* dispatch semantic review is not evidence
  that the review helped.
- **A clean semantic judgment is not proof the implementation is correct.**

## 1. Pick the profile before you start

Choose one profile and record it. Do not switch profiles mid-run; a run that changes route posture
partway through has no single claim it can make.

### Profile A — strict / local-only

**Preconditions:** `mcp_route.observed == true` **and** `registered_profile == "strict"`.

Both halves are required. An unread route (`observed: false`) reports `registered_profile: null`,
which is *unknown*, not *strict* — it is no basis for a zero-egress claim. Observation is also what
separates a strict route from `absent` or `foreign_present`, which report `registered_profile: null`
with `observed: true` and are likewise not Profile A.

Claims this run may make:

- zero external semantic egress through the Yoetz agent route
- deterministic-check behaviour
- receipt honesty — that the refusal is recorded with the exact status/reason and no promoted
  deterministic outcome

Claims this run may **not** make: anything about semantic quality, usefulness, or influence.
Record semantic usefulness as **not tested**. Never record it as "poor", "weak", or "absent" — the
route declined to attempt it, which is not a measurement of it.

### Profile B — policy-enabled

**Preconditions, all three:**

1. `registered_profile == "policy"`
2. `agent_route_semantic_ready == true`
3. human authorization obtained through the existing ceremony — the credential ceremony
   (`yoetz provider credential set`) and privacy policy already in place before the run starts

Claims this run may make: everything in Profile A, plus observations about semantic output, subject
to the provenance gate in §3.

Explicitly: previewing a registration change from inside the session is allowed
(`yoetz integrate codex mcp preview`). **Widening privacy policy from the agent channel is not.**
Policy widening is a human ceremony; if a run finds it needs wider policy, the run stops and is
re-planned, it does not widen and continue.

## 2. Preflight sequence

Run all five before the first task action and record the output verbatim. Every command here is
read-only and already exists — this runbook adds no command.

```text
yoetz version
yoetz service status
yoetz provider status --json
yoetz integrate codex mcp status --json
yoetz privacy show
```

Fields to record from `yoetz provider status --json`:

| Field | What it settles |
|---|---|
| `semantic_ready` | Whether **this installation** can dispatch semantic review at all. Installation-local; unchanged by route posture. |
| `mcp_route.registered_profile` | Which route the Codex agent actually gets: `policy`, `strict`, or `null` when unread. |
| `mcp_route.configured_profile` | Which route setup would register now. |
| `mcp_route.observed` | `false` means the route could not be read — **not** that none is registered. |
| `agent_route_semantic_ready` | Whether the **registered Codex route** can dispatch semantic review. This is the field that selects the profile. |

`registered_profile != configured_profile` is registration drift: the registered entry no longer
matches what this installation's configuration would produce. Record it and resolve it before
starting, through a fresh digest-bound re-registration — `yoetz integrate codex mcp preview`, then
`yoetz integrate codex mcp install --accept --preview-digest <digest>` (ADR-018 decision 7). Do not
assume the configured value is what the agent will get.

`mcp_route.observed: false` is disqualifying for **both** profiles. An unread route is not a policy
route, and it is not a strict route either — it is no route at all until it is read.

`yoetz integrate codex mcp status --json` reports `route_profile` for the same reason, and is the
narrower check when you only need the route.

## 3. The provenance gate

Whether a run may score semantic usefulness is derived from the check result, never asserted from
configuration. The rule is protocol-enforced by `validate_semantic_provenance_binding`
(`src/yoetz/protocol/models.py`) and its totality is locked by test.

**Read provenance together with `semantic_status` and `semantic_reason` — never alone.** Null
provenance means *no provider attempt was made* only on the branches where the protocol forbids
provenance (rows 1 and 3 below). `failed` is unconstrained: it may carry provenance or not, and
either way the attempt is indeterminate. Nor does the converse hold — provenance being *present*
proves an attempt happened, not that it was useful.

| `semantic_status` | `semantic_provenance` | May score semantic usefulness |
|---|---|---|
| any pre-dispatch status — `not_requested`, `not_configured`, `blocked_by_policy` (incl. `route_semantic_ceiling`), `blocked_forbidden_data`, `classification_uncertain`, `awaiting_human`, `human_denied`, `approval_expired` | `null` (enforced) | **No** — not attempted |
| `succeeded`, `refused`, `timeout`, `invalid`, `late`, `stale`; or `unavailable` with `transport_unavailable` / `provider_rate_limited` / `provider_quota_exhausted` | present (enforced) | Yes |
| `unavailable` with `credential_unavailable`, `endpoint_profile_unavailable`, `retry_budget_exhausted`, `audit_reservation_unavailable`, `receipt_persistence_unknown` | `null` (enforced) | **No** — not attempted |
| `failed` / `coordinator_failure` | either — unconstrained | **No** — attempt indeterminate |

`failed` is the one status where provenance presence proves nothing in either direction. Record it
as **attempt indeterminate**, never as "not attempted".

Read the gate off the result, not off preflight. Passing preflight makes an attempt *possible*; only
the result says whether one *happened*.

## 4. What passing preflight does not prove

- It does not prove a semantic review ran. That is the gate in §3.
- It does not prove the review was useful. Availability is not usefulness.
- It does not prove the implementation under review is correct. A clean semantic judgment is one
  observation, not a verdict.
- It does not measure whether feedback changed the agent's behaviour. Influence measurement is
  governed by the [influence dogfood runbook](influence-dogfood.md) (issue
  [#133](https://github.com/TheGaySupreme123/yoetz/issues/133)): four separate evidence streams,
  seeded-defect and miss taxonomy, and a forbidden-summary rule so zero influence cannot be sold as
  improvement.

## 5. Report hygiene

A dogfood report is shared material. It carries:

- credential state as **presence only** (`connected` / `not stored` / `unknown`) — never a value,
  prefix, length, or any property of the stored secret
- no absolute paths, usernames, or machine identifiers
- no transcripts
- no unredacted provider request or response payloads
- versions, bounded status/reason tokens, digests, and structural state

Report the declared profile, the preflight output, the resulting `semantic_status` /
`semantic_reason` / provenance presence, and the gate row those land in. State the claim the run was
authorized to make and stop there.

## See also

- [Codex integration runbook](codex-integration.md) — installing the skill and registering MCP.
- [Privacy and semantic review](../usage/privacy-and-semantic-review.md) — the durable policy that
  authorizes disclosure.
- [Providers](../usage/providers.md) — the readiness conditions behind `semantic_ready`.
- [ADR-018](../adr/ADR-018-host-declared-mcp-route-egress-ceiling.md) — why the strict route is a
  process-local ceiling and not a privacy policy.

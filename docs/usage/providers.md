# Providers and credentials

Binding a provider is nonsecret configuration. Provisioning a credential is a separate, deliberately
awkward ceremony. The two never mix.

## Reviewed presets

```text
yoetz provider endpoint --provider openai            # or: fireworks, anthropic, gemini,
                        --model <model-id>           #     openrouter, grok, vercel-ai-gateway
                                                    #     grok aliases: xai, x-ai
```

Shorthands: `--official`, `--fireworks`, and `--grok` (Grok / xAI).

Reviewed presets use each provider's documented compatible wire style where applicable. **Every
reviewed preset resolves to a real runtime factory** — a preset you can select is a preset Yoetz can
dispatch.

That is not the same as verified. Yoetz does not advertise any non-official preset as a confirmed
working endpoint. A prior one-off Fireworks dogfood dispatch is useful provenance for that run but
does not close the release capability gate. That claim stays gated by the exact evidence described
in [ADR-006](../adr/ADR-006-semantic-provider-profile.md).

## Interactive model choices

On a local terminal, omit `--model` to choose from a short list or enter a custom ID:

```text
yoetz provider endpoint --provider anthropic
yoetz --set --provider anthropic
```

The endpoint menu, explicit interactive provider selectors, and secure `--set` paths share the same
picker for all seven reviewed presets. Choice 1 is the preset's existing default, preserving the
previous Enter-to-accept behavior. The remaining entries are a repository-reviewed sample of
recent provider-recommended/current model families, never more than ten total, followed by an
explicit **Custom model ID** option. No popularity ranking is claimed.

The catalog was reviewed on 2026-08-10 against the provider-owned model sources: OpenAI
[model guidance](https://developers.openai.com/api/docs/guides/latest-model), Fireworks
[Responses API documentation](https://docs.fireworks.ai/guides/response-api), Anthropic
[models overview](https://platform.claude.com/docs/en/about-claude/models/overview), Google
[latest Gemini models](https://ai.google.dev/gemini-api/docs/latest-model), OpenRouter's
[model catalog contract](https://openrouter.ai/docs/guides/overview/models), xAI's
[model list](https://docs.x.ai/developers/models), and Vercel's
[AI Gateway model catalog](https://vercel.com/ai-gateway/models).
The Fireworks list also retains `accounts/fireworks/models/minimax-m3`, which has prior
repository-recorded live semantic provenance. The lists are static so setup stays deterministic
and opens no new network or credential channel; they can age, may not match account entitlements,
and do not establish Yoetz's exact structured-output compatibility. Use the custom entry for any
new, private, preview, region-specific, or omitted model.

Scripts remain explicit and noninteractive:

```text
yoetz provider endpoint --provider anthropic --model claude-sonnet-5 --no-interactive
yoetz --set --provider anthropic --model claude-sonnet-5
```

The first command writes only nonsecret configuration. The second still requires a local terminal
because it continues into hidden credential input; `--model` itself is preserved exactly in both.

## Owner-declared endpoints

For a proxy or self-hosted OpenAI-compatible endpoint
([ADR-014](../adr/ADR-014-toml-settings-and-owner-declared-endpoint.md)):

```text
yoetz provider endpoint --https-origin https://llm.example.com:8443 --model my-proxy-model
```

`https://host[:port]` only — no path, no scheme substitution, no plaintext HTTP.

## `config.toml`

Both paths write the same file. It contains no secrets.

Reviewed preset:

```toml
schema_version = "1"
profile = "local-openai"

[provider]
provider_id = "openai"
endpoint_profile_id = "openai-responses"
endpoint_profile_version = "1.0.0"
model = "gpt-4.1-mini"
capability_profile = "openai-responses-structured-1"
```

Owner-declared HTTPS origin:

```toml
schema_version = "1"
profile = "local-openai"

[provider]
provider_id = "openai-compatible"
endpoint_profile_id = "owner-declared-openai-responses"
endpoint_profile_version = "1.0.0"
model = "my-proxy-model"
capability_profile = "openai-responses-structured-1"

[provider.owner_declared_endpoint]
https_origin = "https://llm.example.com:8443"
# no api_key / headers / path / http — credentials stay in the ceremony
```

You can edit `config.toml` by hand. `/provider` in the terminal interface writes the same binding:
it shows the endpoint and privacy posture before asking for anything secret, and states that
storing a key does not switch external review on.

## Readiness (`yoetz provider status`)

Before spending a run on semantic review, ask whether the five structural conditions hold:

```text
yoetz provider status --json
```

The report names each condition and the exact next command when one is known to be unmet. Five are
**installation capability** conditions, a sixth is the exact current-repository grant, and a
separate verdict describes the Codex agent route.

1. the local service is running and unlocked
2. `verification.semantic` is not `disabled`
3. a provider endpoint is bound in `config.toml`
4. **the bound provider's** credential is connected (service capability `external_provider`)
5. the machine privacy ceiling enables the `llm_inference` channel
6. the repository derived from the trusted current session has an exact granted row beneath that
   ceiling

Condition 4 is per-provider, not "any credential". If you rebind the endpoint from one preset to
another and do not run the credential ceremony for the new one, the old credential does not carry
over: readiness stays false and checks report `credential_unavailable` rather than a
misleading ready state.

Conditions 4 and 5 are independent. Closing only one moves the failure without making semantic
review work — the check reason changes, the outcome does not.

When the service is locked, credential and privacy state are `unknown`, not incomplete. Unknown
conditions have no remediation command. For `vault_mode=uninitialized`, continue with `yoetz setup`;
for an existing locked vault, use `yoetz service unlock`; and when the scoped platform entry is stale
or rejected, use `yoetz service auto-unlock repair`. The JSON field `readiness_determinable`
distinguishes a known not-ready state from one that cannot yet be read.

`semantic_ready: true` means all six structural conditions hold for this repository-bound service
view. It does not prove live provider dispatch. `repository_grant_state` and
`repository_migration_state` expose the authority inputs separately; an omitted trusted locator
makes repository state unknown and readiness false rather than inheriting the machine ceiling.

### The agent route is a separate verdict

`semantic_ready` covers the six conditions above and nothing else. It says this repository-bound
service view has the provider, machine-ceiling, and exact repository authority needed for semantic
review. It does not say the Codex agent gets a route that will dispatch.

Yoetz registers Codex with one of two serve commands, and both classify as `yoetz_owned` — so
registration state alone cannot tell them apart. The report therefore names the route directly:

```json
"mcp_route": {
  "registration_state": "yoetz_owned",
  "registered_profile": "strict",
  "configured_profile": "policy",
  "observed": true
},
"repository_grant_state": "granted",
"repository_migration_state": "not_applicable",
"agent_route_semantic_ready": false
```

- `registered_profile` — the observed Yoetz route: `policy`, `strict`, or `null`. **`null` has two
  different meanings**, and `registration_state` plus `observed` are what tell them apart: with
  `observed: true` it means no Yoetz route is registered (`registration_state` is `absent` or
  `foreign_present`); with `observed: false` it means the route could not be read. Do not read a
  missing or foreign registration as a probe failure.
- `configured_profile` — what setup would register now. A mismatch is registration drift. Fixing it
  takes both steps of the digest-bound ceremony: `yoetz integrate codex mcp preview` produces the
  digest, then `yoetz integrate codex mcp install --accept --preview-digest <digest>` applies it.
  Preview alone changes nothing.
- `observed: false` — the route could not be read. That is *unknown*, not *absent*, and it is never
  reported as a blocker.
- `repository_grant_state` / `repository_migration_state` — the exact trusted-session repository
  authority inputs. Public `workspace_ref` never supplies them.
- `agent_route_semantic_ready` — `semantic_ready` **and** `registered_profile == "policy"`. An
  unread route therefore makes it `false`: `registered_profile` is `null`, so an unobserved route
  is never treated as a policy route.

**A strict registration does not make this repository-bound service view not-ready.** The strict
route is a
process-local ceiling (ADR-018): it stops that one MCP process from requesting semantic review. A
`yoetz check` from the CLI, or a check from the terminal interface, still dispatches normally. So
`semantic_ready: true` alongside a strict route is not a contradiction — it means repository-bound
capability exists while that Codex agent route cannot dispatch. A strict route adds a
`mcp_route_profile` blocker marked `scope: "agent_route"`, and leaves the exit code alone.

Neither verdict substitutes for the other. Reading `semantic_ready: true` and expecting semantic
review through a strict agent route is exactly the conflation this field exists to prevent; see the
[semantic dogfood runbook](../runbooks/semantic-dogfood.md) for the preflight that consumes it.

### Where the route verdict is reported

Both verdicts and the repository-authority inputs appear wherever readiness is shown, never merged:

- `yoetz provider status` (human and `--json`).
- The terminal interface's readiness layers, as a separate `Codex agent route permits deeper
  review` line beside `Repository grant` and `Deeper review ready`. An unread registration or
  unbound repository renders as unknown rather than as inherited authority.
- `yoetz privacy setup` and the terminal interface's `/privacy`: when a committed policy permits
  external review and the registered route is `strict`, the ceremony names the mismatch and the
  command that fixes it. That note is advisory — it never fails the ceremony, changes an exit
  code, or appears when the route cannot be read.

The last one exists because machine ceiling, repository grant, and registration are separate facts
changed by different transitions. Moving from local-only to assisted review with an older strict
registration in place produces a correct policy and a Codex session where every check returns
`blocked_by_policy` / `route_semantic_ceiling` — accurate, and impossible to act on without being
told which of the two is the cause.

The service derives repository identity from the client's actual/configured working directory,
resolves Git's common root, commits it under the installation key, and discards the raw path.
Branches and linked worktrees share a grant; independent clones do not. Upgrades preserve accepted
machine bytes and may only consume the bounded legacy carry-forward described in ADR-009. None of
these structural verdicts substitutes for the installed-wheel two-repository semantic and receipt
proof still outstanding under issue #139.

## The credential ceremony

```text
yoetz provider credential set
```

The API credential is provisioned through a confidential terminal ceremony and stored by the trusted
local service. It is **never** a command-line flag, a file path, an environment variable, a config
value, a log line, a trace, a transcript, or anything reachable from LLM context
([ADR-015](../adr/ADR-015-elevated-bootstrap-consent.md),
[ADR-016](../adr/ADR-016-human-review-non-default-actions.md)).

If you find yourself wanting to pass a key as a flag, that is the design working as intended.

`/provider` in the terminal interface uses this exact ceremony and has no credential path of its
own. It asks for explicit consent, then suspends the full-screen interface and hands the
controlling terminal to the ceremony, which opens `/dev/tty` and disables echo itself. No secret
byte enters the interface's state, transcript, logs, or any snapshot. Where an environment cannot
suspend, the interface says so and names this command rather than offering to take the key through
the window.

## Binding a provider does not enable egress

A bound provider plus a stored credential still sends nothing until privacy policy permits it. See
[Privacy and semantic review](privacy-and-semantic-review.md). Every network channel is
independently authorized.

**Configuration is also not readiness.** Storing a binding and a credential are two facts; a
working provider is a third, and it is only established by a successful probe. The terminal
interface reports them separately and always has:

```text
✓ Provider binding saved
✓ API key stored securely
! Live provider connection has not been tested
! External semantic review is not yet proven ready
```

This build exposes no bounded live provider probe from the local service, so a connection test in
the interface reports itself as unavailable rather than reporting a pass
([ADR-017](../adr/ADR-017-full-screen-terminal-interface.md), *Known limitations*). A provider
that fails never downgrades local deterministic readiness.

## Checking what you have

```text
yoetz setup status        # read-only posture, mutates nothing
yoetz service status      # is the service up, is the vault unlocked
yoetz version --json      # installed package and runtime identity
```

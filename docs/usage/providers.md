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

The catalog was reviewed on 2026-07-28 against provider-owned sources: OpenAI
[model guidance](https://developers.openai.com/api/docs/guides/latest-model), Fireworks
[Responses API documentation](https://docs.fireworks.ai/guides/response-api), Anthropic
[models overview](https://platform.claude.com/docs/en/about-claude/models/overview), Google
[latest Gemini models](https://ai.google.dev/gemini-api/docs/latest-model), OpenRouter's
[model catalog contract](https://openrouter.ai/docs/guides/overview/models), xAI's
[model list](https://docs.x.ai/developers/models), and Vercel's
[AI Gateway model discovery documentation](https://vercel.com/docs/ai-gateway/models-and-providers).
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

You can edit `config.toml` by hand. Bare `yoetz` also exposes the same binding under **LLM provider**
in the interactive menu.

## Readiness (`yoetz provider status`)

Before spending a run on semantic review, ask whether the five structural conditions hold:

```text
yoetz provider status --json
```

The report names each condition and the exact next command when one is known to be unmet:

1. the local service is running and unlocked
2. `verification.semantic` is not `disabled`
3. a provider endpoint is bound in `config.toml`
4. **the bound provider's** credential is connected (service capability `external_provider`)
5. the effective privacy policy enables the `llm_inference` channel

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

`semantic_ready: true` is structural readiness only. It does not prove live provider dispatch.

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

## Binding a provider does not enable egress

A bound provider plus a stored credential still sends nothing until privacy policy permits it. See
[Privacy and semantic review](privacy-and-semantic-review.md). Every network channel is
independently authorized.

## Checking what you have

```text
yoetz setup status        # read-only posture, mutates nothing
yoetz service status      # is the service up, is the vault unlocked
yoetz version --json      # installed package and runtime identity
```

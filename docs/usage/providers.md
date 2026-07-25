# Providers and credentials

Binding a provider is nonsecret configuration. Provisioning a credential is a separate, deliberately
awkward ceremony. The two never mix.

## Reviewed presets

```text
yoetz provider endpoint --provider openai            # or: fireworks, anthropic, gemini,
                        --model <model-id>           #     openrouter, vercel-ai-gateway
```

Shorthands: `--official` (Official OpenAI Responses) and `--fireworks`.

Reviewed presets use each provider's documented compatible wire style where applicable. **Every
reviewed preset resolves to a real runtime factory** — a preset you can select is a preset Yoetz can
dispatch.

That is not the same as verified. None of the non-official presets has recorded live evidence yet,
so Yoetz does not claim any of them as a confirmed working endpoint. That claim stays gated by the
exact capability evidence described in [ADR-006](../adr/ADR-006-semantic-provider-profile.md).

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

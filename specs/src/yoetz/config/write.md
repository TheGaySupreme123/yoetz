# src/yoetz/config/write.py — atomic nonsecret config.toml writers

**Wave:** C | **ADRs:** ADR-006, ADR-009, ADR-014 | **Imports (spec-tree):**
`config/models.md`, `config/paths.md`, `config/privacy.md` | **Imported by:**
`cli/provider_binding.md`, `cli/setup.py.md`, `cli/menu.md`, `cli/app.md`

## Purpose

Provide the sole writers that turn validated `YoetzConfig` / provider bindings into service-owned
`config.toml`. CLI and menu are editors of this surface; they do not invent a parallel authority.
The bundled provider choices are exact, nonsecret endpoint profiles; they do not accept a free URL.

## Public surface

- `official_openai_provider(...)` / `fireworks_provider(...)` /
  `anthropic_provider(...)` / `google_gemini_provider(...)` / `openrouter_provider(...)` /
  `vercel_ai_gateway_provider(...)` / `owner_declared_openai_provider(...)` — construct exact
  nonsecret `ProviderProfileConfig` values for the reviewed provider choices or an owner-declared
  HTTPS origin.
- `PROVIDER_PRESETS` / `provider_preset(...)` — resolve the exact nonsecret host, base path,
  protocol style, endpoint-profile identity, and suggested model for a setup choice.
- `render_config_toml(config) -> str` — deterministic TOML text; round-trips through
  `YoetzConfig.model_validate`.
- `write_config_toml(config, path=None) -> Path` — atomic replace; default path uses
  `config_file_path()` with owner-only directory enforcement.
- `write_provider_binding(provider, *, profile="local-openai", path=None, base=None) -> Path` —
  merge provider into config and write.
- `default_capability_profile() -> str` — shared Responses structured capability id.

The bundled endpoint matrix is:

| choice | provider/profile | exact HTTPS endpoint | default model | wire style |
|---|---|---|---|---|
| `official_openai` | `openai/openai-responses` | `api.openai.com/v1/responses` | `gpt-4.1-mini` | Responses |
| `fireworks` | `fireworks/fireworks-responses` | `api.fireworks.ai/inference/v1/responses` | `accounts/fireworks/models/qwen3-235b-a22b` | Responses |
| `anthropic` | `anthropic/anthropic-openai-chat-completions` | `api.anthropic.com/v1/chat/completions` | `claude-sonnet-4-6` | Chat Completions |
| `google_gemini` | `google/google-gemini-openai-chat-completions` | `generativelanguage.googleapis.com/v1beta/openai/chat/completions` | `gemini-3.6-flash` | Chat Completions |
| `openrouter` | `openrouter/openrouter-openai-chat-completions` | `openrouter.ai/api/v1/chat/completions` | `openai/gpt-5.2` | Chat Completions |
| `vercel_ai_gateway` | `vercel-ai-gateway/vercel-ai-gateway-openai-chat-completions` | `ai-gateway.vercel.sh/v1/chat/completions` | `anthropic/claude-sonnet-4-6` | Chat Completions |

Suggested models are editable nonsecret defaults, not capability evidence. Every credential still
goes through the hidden local-terminal ceremony and the service vault.

## Behavior

- Never writes credentials, headers, free `base_url`, or vault material.
- Omits the `[privacy]` bootstrap section when it equals the safe default (generation-1 seed stays
  load-default; durable privacy desired-state is the separate `privacy_desired` path).
- Explicit `path=` (tests/tools) may skip platform path-safety directory checks; the default
  service path always enforces them.
- Rendered text is re-validated with `tomllib` + `YoetzConfig` before write.

## Errors and edge cases

- `ConfigError("config_value_invalid")` on I/O failure or unsafe default parent.
- `ConfigError("privacy_bootstrap_unsafe")` if a writer is asked to emit a non-safe `[privacy]`
  bootstrap (durable policy does not go through this writer).

## Invariants

- Secrets cannot appear in rendered TOML (validated models forbid them).
- Official vs owner-declared mutual exclusion is enforced by `ProviderProfileConfig`.

## Tests

- `tests/unit/config/test_owner_declared_endpoint.py`

## Open questions

None — remaining live owner-declared host probe is E-016 optional evidence, not a writer gap.

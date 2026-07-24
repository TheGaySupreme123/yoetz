# src/yoetz/config/write.py — atomic nonsecret config.toml writers

**Wave:** C | **ADRs:** ADR-006, ADR-009, ADR-014 | **Imports (spec-tree):**
`config/models.md`, `config/paths.md`, `config/privacy.md` | **Imported by:**
`cli/provider_binding.md`, `cli/setup.py.md`, `cli/menu.md`, `cli/app.md`

## Purpose

Provide the sole writers that turn validated `YoetzConfig` / provider bindings into service-owned
`config.toml`. CLI and menu are editors of this surface; they do not invent a parallel authority.

## Public surface

- `ProviderPreset` / `PROVIDER_PRESETS` — the closed nonsecret setup registry. Each entry carries
  the exact provider id, endpoint-profile identity/version, capability-profile name, host, fixed
  path prefix, default model, and API style.
- `provider_preset(choice) -> ProviderPreset` — resolve a reviewed preset or raise
  `ConfigError("config_value_invalid")`.
- `official_openai_provider(...)` / `fireworks_provider(...)` /
  `anthropic_provider(...)` / `google_gemini_provider(...)` /
  `openrouter_provider(...)` / `vercel_ai_gateway_provider(...)` /
  `owner_declared_openai_provider(...)` — construct exact nonsecret `ProviderProfileConfig`
  values for bundled presets or an owner-declared HTTPS origin.
- `render_config_toml(config) -> str` — deterministic TOML text; round-trips through
  `YoetzConfig.model_validate`.
- `write_config_toml(config, path=None) -> Path` — atomic replace; default path uses
  `config_file_path()` with owner-only directory enforcement.
- `write_provider_binding(provider, *, profile="local-openai", path=None, base=None) -> Path` —
  merge provider into config and write.
- `default_capability_profile() -> str` — shared Responses structured capability id.

## Behavior

- Never writes credentials, headers, free `base_url`, or vault material.
- Bundled setup identities are fixed: Anthropic `api.anthropic.com/v1` and Google Gemini
  `generativelanguage.googleapis.com/v1beta/openai` use OpenAI-compatible Chat Completions;
  OpenRouter `openrouter.ai/api/v1` uses Chat Completions; Vercel AI Gateway
  `ai-gateway.vercel.sh/v1` uses OpenAI-compatible Responses. The path prefix is registry
  metadata, never a user-editable URL.
- These presets make nonsecret binding and credential targeting clear. Each one resolves to a
  runtime factory in `adapters/providers/factory.md`, so no bundled choice can be written here and
  then fail at dispatch with `factory_unavailable`. Being dispatchable is not being verified: an
  exact model/endpoint capability fixture and live evidence are still required by ADR-006/E-007
  before release support is advertised.
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

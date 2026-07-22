# src/yoetz/config/write.py — atomic nonsecret config.toml writers

**Wave:** C | **ADRs:** ADR-006, ADR-009, ADR-014 | **Imports (spec-tree):**
`config/models.md`, `config/paths.md`, `config/privacy.md` | **Imported by:**
`cli/provider_binding.md`, `cli/setup.py.md`, `cli/menu.md`, `cli/app.md`

## Purpose

Provide the sole writers that turn validated `YoetzConfig` / provider bindings into service-owned
`config.toml`. CLI and menu are editors of this surface; they do not invent a parallel authority.

## Public surface

- `official_openai_provider(...)` / `fireworks_provider(...)` /
  `owner_declared_openai_provider(...)` — construct exact
  nonsecret `ProviderProfileConfig` values for Official OpenAI vs owner-declared HTTPS origin.
- `render_config_toml(config) -> str` — deterministic TOML text; round-trips through
  `YoetzConfig.model_validate`.
- `write_config_toml(config, path=None) -> Path` — atomic replace; default path uses
  `config_file_path()` with owner-only directory enforcement.
- `write_provider_binding(provider, *, profile="local-openai", path=None, base=None) -> Path` —
  merge provider into config and write.
- `default_capability_profile() -> str` — shared Responses structured capability id.

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

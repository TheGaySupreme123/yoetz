# src/yoetz/cli/provider_binding.py — nonsecret LLM endpoint binding prompts

**Wave:** D/F | **ADRs:** ADR-006, ADR-012, ADR-013, ADR-014 | **Imports (spec-tree):**
`config/write.md`, `config/models.md`, `config/paths.md` | **Imported by:**
`cli/setup.py.md`, `cli/menu.md`, `cli/app.md`

## Purpose

Collect Official OpenAI vs owner-declared HTTPS origin+model (never secrets) and write the same
`config.toml` fields the user could edit by hand.

## Public surface

- `apply_provider_endpoint_choice(choice, *, model, https_origin=None, path=None)`
- `prompt_provider_endpoint_binding(*, path=None)`
- `NEXT_CREDENTIAL` — exact next-step string pointing at the credential ceremony
- `ProviderEndpointChoice` — `official_openai` | `fireworks` | `owner_declared`

## Behavior

Interactive prompts never ask for API keys. The reviewed Fireworks choice binds
`api.fireworks.ai/inference/v1` without accepting a free path. Writes go through
`config/write.write_provider_binding`.
Credentials remain `yoetz provider credential set|rotate`. Owner-declared selections print that
data-use posture is `unknown` and never inherits `assisted`.

## Errors and edge cases

- Invalid origin/model → bounded `ConfigError` reason echoed as `invalid_request`.
- Skip (`s`) returns without writing.

## Invariants

- No secret ingress on this surface.
- Written fields match hand-edited TOML validation rules.

## Tests

- `tests/unit/config/test_owner_declared_endpoint.py`

## Open questions

None.

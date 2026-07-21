# src/yoetz/config/privacy_desired.py — privacy desired-state TOML

**Wave:** C | **ADRs:** ADR-009, ADR-014 | **Imports (spec-tree):** `config/models.md`,
`domain/privacy.md` | **Imported by:** `cli/app.md` (`privacy export-desired` / `apply-desired`)

## Purpose

Encode/decode nonsecret durable privacy policy intent as a sidecar TOML document
(`schema = "yoetz.privacy-desired/1"`). Editing the file never silently widens egress.

## Public surface

- `PRIVACY_DESIRED_SCHEMA` — exactly `yoetz.privacy-desired/1`.
- `render_privacy_desired_toml(policy)` / `write_privacy_desired_toml(policy, path)`
- `load_privacy_desired_canonical(path) -> bytes` — embedded canonical policy JSON bytes

## Behavior

The document embeds one canonical policy JSON string under `[privacy.desired].policy_json`.
Timestamps use `format_rfc3339_millis`. Apply is performed by CLI: load candidate, read effective
policy from the service, classify with `is_privacy_tightening`.

| Class | Behavior |
|---|---|
| equivalent | no-op success |
| tighten | report; route to existing `yoetz privacy tighten` gate (file alone does not mutate) |
| widen | fail closed; require `propose` → `decide-policy` |

## Errors and edge cases

- Wrong schema → `ConfigError("config_schema_unsupported")`.
- Malformed TOML/JSON → `ConfigError("config_value_invalid")`.

## Invariants

- File edits alone never mutate the durable policy store.
- Widen never auto-commits.

## Tests

- `tests/unit/config/test_privacy_desired.py`

## Open questions

None.

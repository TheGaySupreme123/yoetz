# tests/unit/config/test_load_precedence.py — config loading precedence and refusal rules

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/config/load.md`, `src/yoetz_core/config/models.md`, `src/yoetz_core/config/paths.md`
**Imported by:** the config unit suite

## Purpose

Lock the precedence rules for config loading so the process reads the intended source and refuses
unsafe overrides.

## Public surface

- `test_env_overrides_file_when_allowed` — explicit environment inputs beat file defaults where
  the contract allows.
- `test_user_config_cannot_enable_release_probe` — CI-only profiles are refused from user files.
- `test_duplicate_keys_are_rejected_before_model_build` — parser-level duplicates fail closed.
- `test_storage_path_safety_is_deferred_to_paths_module` — model loading stays pure and defers path
  safety checks.

## Behavior

The suite proves:

- precedence is explicit and deterministic;
- user config cannot sneak in CI-only runtime modes;
- duplicate keys and malformed documents are rejected before model construction;
- model loading does not touch the filesystem to prove path safety.

## Errors and edge cases

- A loader that silently chooses the wrong source fails.
- A path safety check executed too early fails.

## Invariants

1. Config precedence is explicit.
2. Parser failures are closed.
3. I/O-free model loading stays I/O-free.

## Tests

- `tests/unit/config/test_load_precedence.py`

## Open questions

None.

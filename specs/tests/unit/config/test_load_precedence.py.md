# tests/unit/config/test_load_precedence.py — config loading precedence and refusal rules

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/config/load.md`, `src/yoetz/config/models.md`, `src/yoetz/config/paths.md`
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
- `test_secret_env_name_wins_before_unknown_without_value_reads` — key-only secret scanning runs
  before unknown-name validation and no environment value is fetched on either failure path.
- `test_loader_parses_selected_env_scalars_before_strict_model` — the loader converts selected
  base-10 integer/path values to their exact Python types, while direct strict-model string coercion
  remains forbidden.

## Behavior

The suite proves:

- precedence is explicit and deterministic;
- user config cannot sneak in CI-only runtime modes;
- duplicate keys and malformed documents are rejected before model construction;
- model loading does not touch the filesystem to prove path safety.
- environment key names are sorted and scanned in two passes; a secret-shaped `YOETZ_` name wins
  over an unrelated unknown name, reports only its key name, and a sentinel mapping proves no
  `__getitem__`/value iteration occurs before all names are accepted.
- precedence selects one leaf value before explicit loader parsing; shadowed lower-precedence
  strings cannot trigger model coercion, and the strict model receives no raw integer/path string.

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

# tests/conformance/surfaces/test_cli_contract_matrix.py — CLI contract matrix

**Wave:** D | **ADRs:** ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/cli/app.md`, `src/yoetz_core/cli/exits.md`
**Imported by:** conformance surface tests

## Purpose

Prove the CLI command matrix, JSON mode, help text, and exit codes match the public contract.

## Public surface

- `test_command_matrix_matches_six_operations` — all required commands exist.
- `test_json_and_human_output_modes` — structured and human paths stay distinct.
- `test_exit_code_matrix` — public codes map to the expected exits.

## Behavior

The test asserts:

- the six operation commands and support commands are present;
- `--json` output is structured and bounded;
- human output never outruns structured truth;
- exit codes match the frozen mapping for success, invalid input, pending, conflict, provider,
  internal, and cancellation cases.

## Errors and edge cases

- A command that appears only in help text but not in the app fails.

## Invariants

1. CLI command matrix is frozen.
2. JSON mode stays structured.
3. Exit code mapping is explicit.

## Tests

- `tests/conformance/surfaces/test_cli_contract_matrix.py`

## Open questions

None.

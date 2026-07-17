# tests/conformance/surfaces/test_cli_contract_matrix.py — CLI contract matrix

**Wave:** D | **ADRs:** ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/cli/app.md`, `src/yoetz/cli/exits.md`
**Imported by:** conformance surface tests

## Purpose

Prove the CLI command matrix, JSON mode, help text, and exit codes match the public contract.

## Public surface

- `test_command_matrix_matches_six_operations` — all required commands exist.
- `test_privacy_command_and_recipe_matrix` — trusted-local privacy setup/audit commands, five
  recipes, thirteen-answer expansion, and recommendation eligibility are exact.
- `test_idle_relock_command_is_confidential_only` — the exact target grammar is advertised and the
  command is absent from ordinary control and MCP registries.
- `test_json_and_human_output_modes` — structured and human paths stay distinct.
- `test_exit_code_matrix` — every member of the closed `PublicErrorCode` enum maps to the exact
  expected exit and no table key is missing or extra.

## Behavior

The test asserts:

- the six operation commands and support commands are present;
- `service idle-relock <60..86400|disabled>` is present only as a trusted-foreground command and
  maps to YZH1 `idle_relock_policy_change`, never a `ControlMethod`;
- `privacy setup|show|propose|tighten` and `privacy receipts list|get` are present while decision
  commands remain confined to trusted human control;
- `--json` output is structured and bounded;
- human output never outruns structured truth;
- exit codes match the frozen mapping for success and all 22 public codes, including protocol
  incompatibility, not-found, event invalidity, limits, busy, every storage/provider state,
  privacy authority, semantic-result invalidity, internal error, and cancellation.

## Errors and edge cases

- A command that appears only in help text but not in the app fails.
- An idle-relock alias, noncanonical/range-invalid target accepted by help or parser, ordinary
  control token, MCP descriptor, `--yes`, or piped-decision path fails.
- A missing/extra `PUBLIC_EXIT_CODES` member or family-only sample that fails to enumerate the
  closed public enum fails.
- A recipe that hides an expanded answer, recommends an ineligible endpoint, or silently commits a
  policy fails.

## Invariants

1. CLI command matrix is frozen.
2. JSON mode stays structured.
3. Exit code mapping is explicit.
4. CLI convenience never becomes privacy authority.
5. Idle-relock target selection is not idle-relock authority; only the confidential human ceremony
   can apply it.

## Tests

- `tests/conformance/surfaces/test_cli_contract_matrix.py`

## Open questions

None.

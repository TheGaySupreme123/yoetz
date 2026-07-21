# src/yoetz/cli/setup.py — first-run setup wizard orchestration

**Wave:** D/F | **ADRs:** ADR-007, ADR-008, ADR-009, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/integrations/codex_discovery.py.md`,
`specs/src/yoetz/adapters/integrations/codex_mcp.py.md`,
`specs/src/yoetz/application/harness_mcp.py.md`, `specs/src/yoetz/config/paths.md`,
`specs/src/yoetz/ports/harness_mcp.py.md` | **Imported by:** `specs/src/yoetz/cli/app.md`

## Purpose

Owns the ADR-012 wizard and the client-local `integrate <harness> mcp` command bodies, kept out
of `app.py` the way `unlock.py` and `privacy_control.py` hold their logic behind thin wiring.
The wizard connects existing safe operations; it automates no ceremony and spawns no service.

## Public surface

- `SETUP_MARKER_SCHEMA` — exactly `yoetz.setup-wizard-marker/1`.
- `setup_marker_present() -> bool` — marker existence, failing closed (`True`) on
  `PathSafetyError`/`OSError` so an unsafe state directory never triggers the wizard.
- `should_offer_first_run() -> bool` — true only when stdin and stdout are both TTYs and the
  marker is absent.
- `run_setup_wizard(*, non_interactive, codex_path, accept, json_output) -> int` (async).
- `setup_status(*, json_output) -> int` (async) — read-only posture,
  schema `yoetz.setup-status/1`.
- `integrate_mcp(action, harness, *, codex_path, accept, preview_digest, json_output) -> int`
  (async) — `status|preview|install` for harness exactly `codex`.

## Behavior

The wizard: (1) discovers Codex binaries; (2) selects one — explicit `--codex-path` (must be an
existing executable file), the single candidate, an interactive numbered choice for several, or
a fail-closed usage error (`exit 2`, message naming `--codex-path`) when several exist
non-interactively; zero candidates skip registration with reason `codex_not_found`; (3) runs the
registration step through `HarnessMcpService`+`CodexMcpAdapter` — `yoetz_owned` reports
`already_registered`, `foreign_present` reports `skipped`/`foreign_entry_present` (preserved,
never replaced), otherwise an interactive confirm (or the `--accept` flag) gates one
digest-bound `register`; adapter failures become `failed` with the reason token; (4) probes
service reachability via `build_service_client().service_status()` (a `ControlError` is
`reachable: false`; the CLI never spawns the service); (5) assembles `next_steps` naming the
exact follow-up commands — `yoetz service run`, `yoetz service unlock`, `yoetz privacy setup`,
`yoetz provider credential set` — pointing at, never automating, the trusted ceremonies;
(6) on a mutating run (interactive, or `--accept`) writes the marker `{outcome, schema}` as
canonical JSON, mode 0600, at `setup_marker_path()`; a dry run (`--non-interactive` without
`--accept`) writes nothing; (7) emits the report (schema `yoetz.setup-wizard-report/1`) as
canonical JSON in JSON/non-TTY mode or a bounded human summary interactively, and returns 0 for
every completed run — partial outcomes are reported honestly, not encoded as failures.

`setup_status` reports discovered binaries with per-binary registration state (adapter errors
become `registration_state: null` plus the reason token), marker presence, and service
reachability; it mutates nothing.

`integrate_mcp` resolves one binary (explicit path, or exactly one discovered; zero or several
are usage failures), then: `status` prints the state token; `preview` prints action, state,
warnings, and `preview_digest`; `install` optionally binds `--preview-digest` (mismatch is
`preview_stale`), refuses `foreign_present`, requires interactive confirmation or `--accept`
(else `confirmation_required`), no-ops for `yoetz_owned`, and otherwise registers. Errors print
`mcp_registration_<reason>` to stderr and exit 2 for user-correctable reasons
(`confirmation_required|preview_stale|foreign_entry_present`) or 20 for environment failures
(`harness_unavailable|timeout|parse_failed|registration_failed`).

## Errors and edge cases

- No secret-shaped option exists on any command; the wizard never reads or forwards credential
  bytes (locked by the conformance help-text scan).
- TTY probing failures degrade to non-interactive behavior; nothing prompts without a TTY.
- Marker write failures (unsafe path, I/O error) report `marker_written: false` without
  failing the run.
- Executable paths appear only in the local human/JSON report on the user's own terminal
  (`local_human_view`); they never enter diagnostics or exceptions.

## Invariants

1. Every mutation is preceded by a preview and an explicit acceptance bound to its digest.
2. The wizard never spawns the service, never automates `privacy setup` or the credential
   ceremony, and never claims an unverified step succeeded.
3. A dry run mutates nothing: no marker, no registration.
4. Foreign same-name MCP entries are preserved under every path.

## Tests

- `tests/subprocess/test_setup_wizard_cli.py` — dry run, accept-and-register with marker,
  foreign preservation, multi-candidate fail-closed, no-codex guidance, read-only status, the
  `integrate mcp` matrix, secret-option absence, and non-TTY help fallback.
- `tests/conformance/surfaces/test_cli_contract_matrix.py` — `setup` in the frozen command
  matrix and the bare-invocation help regression.

## Open questions

None.

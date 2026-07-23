# src/yoetz/cli/setup.py — first-run setup wizard orchestration

**Wave:** D/F | **ADRs:** ADR-007, ADR-008, ADR-009, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/integrations/codex_discovery.py.md`,
`specs/src/yoetz/adapters/integrations/codex_mcp.py.md`,
`specs/src/yoetz/application/harness_mcp.py.md`, `specs/src/yoetz/config/paths.md`,
`specs/src/yoetz/ports/harness_mcp.py.md` | **Imported by:** `specs/src/yoetz/cli/app.md`

## Purpose

Owns the ADR-012 wizard and the client-local `integrate <harness> mcp` command bodies, kept out
of `app.py` the way `unlock.py` and `privacy_control.py` hold their logic behind thin wiring.
The wizard connects existing safe operations. A local interactive run may start the fixed service
and enter existing hidden-TTY confidential ceremonies; noninteractive setup does neither.

## Public surface

- `run_provider_setup(fireworks=False, model=None, api_key=None) -> int` — the short path used
  by `yoetz --set`; it starts the local service on demand, performs required vault setup/unlock,
  optionally applies the fixed Fireworks profile and model without asking for internal binding
  identifiers, then enters the credential ceremony. An explicit API-key value is never echoed and
  is converted immediately to the mutable one-shot credential buffer; omission uses hidden input.
  A fully supplied `--fireworks --model --api-key` invocation does not require a TTY or prompt.
  On success it reports layer-separated honesty only: binding/credential storage as the supported
  demonstrated layer, then separately whether the optional `semantic-openai` SDK extra is
  importable, that production ready composition still uses `_semantic_not_configured`, and that
  privacy policy, transport probe, and installed-artifact evidence were not demonstrated. It never
  claims the provider is ready for live dispatch or semantic review from storage alone, and it
  does not wire new provider factories to manufacture readiness.

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

The wizard: (1) discovers Codex binaries; (2) on an interactive run with discovered candidates,
presents the automatically detected supported harnesses as a numbered human-facing list — exactly
`Codex` in v0.1 — and asks which harness to connect to `Yoetz`; (3) selects one installation —
explicit `--codex-path` (must be an existing executable file), the single candidate, a separate
interactive numbered choice for several, or a fail-closed usage error (`exit 2`, message naming
`--codex-path`) when several exist non-interactively; zero candidates skip registration with reason
`codex_not_found`; (4) runs **one** Codex integration step through `CodexPluginService` +
`HarnessMcpService`+`CodexMcpAdapter`: preview plugin/guidance/hooks and MCP, one explicit `Y`/`N`
(or `--accept`), install/verify plugin (via the owning plugin service; `allow_untested=True` for
observation hooks), register/verify MCP, then grant observation consent **only** after both verify.
Already-registered MCP (`yoetz_owned`) must **not** return early — plugin install/verify and consent
activation still run. Foreign same-name MCP entries are preserved and skip consent. Modified-plugin
refusals leave consent inactive. (5) on an interactive run, uses the fixed on-demand connector and
reports exact service state; a noninteractive status probe never starts it; (6) on an interactive
TTY, offers Official OpenAI, the fixed Fireworks Responses profile, or an owner-declared HTTPS
origin+model (writes `config.toml` via `cli/provider_binding`), then enters the existing hidden-TTY
vault/provider ceremonies when needed; (7) assembles `next_steps` naming the exact follow-up
commands — `yoetz service run`, `yoetz service unlock`, `yoetz privacy setup`,
`yoetz provider endpoint` / TOML edit, and `yoetz provider credential set`; on a real TTY it
invokes those trusted ceremonies directly and records only structural outcomes;
(8) on a mutating run (interactive, or `--accept`) writes the marker `{outcome, schema}` as
canonical JSON, mode 0600, at `setup_marker_path()`; a dry run (`--non-interactive` without
`--accept`) writes nothing; (9) emits the report (schema `yoetz.setup-wizard-report/1`) as
canonical JSON in JSON/non-TTY mode or a bounded human summary interactively, and returns 0 for
every completed run — partial outcomes are reported honestly, not encoded as failures. The report
includes a `readiness` object with Codex exe/version, MCP registration, plugin installation, hook
presence/trust, consent, service routing, `observation_ready`, and semantic-advice readiness.
“Ready to observe” requires verified plugin/hooks + active consent + service routing; stored
consent alone is insufficient. The human summary reports the same layers as separate structural
lines. Successful MCP registration is phrased as recorded registration with automatic activation
not tested; skill support states when no tested capability profile exists. The summary never claims
or implies that Yoetz is set up or ready on a harness merely because an MCP entry exists.

When `.yoetz/checks.toml` exists, interactive setup parses fixed schema
`yoetz.approved-check-policy/1` and shows the exact raw-byte SHA-256 digest plus check IDs before
project confirmation. That trusted-local confirmation binds only the shown digest; repository bytes
grant no authority. Noninteractive `--accept` may install integration and consent but leaves checks
`untrusted_confirmation_required`. Any byte change suspends all commands until
`yoetz observe checks trust --policy-digest ...` confirms the new preview. Reports expose only the
digest, check IDs, and trust outcome—never workspace path, check output, or secret content.

`setup_status` reports discovered binaries with per-binary registration state (adapter errors
become `registration_state: null` plus the reason token), the same separate skill/plugin/hook/trust
layers, marker presence, and service reachability; it mutates nothing.

Human-facing setup copy capitalizes the product names `Yoetz` and `Codex`. Lowercase `yoetz` and
`codex` remain unchanged in executable names, subcommands, MCP server identifiers, wire values, and
canonical JSON.

`integrate_mcp` resolves one binary (explicit path, or exactly one discovered; zero or several
are usage failures), then: `status` prints the state token; `preview` prints action, state,
warnings, and `preview_digest`; `install` optionally binds `--preview-digest` (mismatch is
`preview_stale`), refuses `foreign_present`, requires interactive confirmation or `--accept`
(else `confirmation_required`), no-ops for `yoetz_owned`, and otherwise registers. Errors print
`mcp_registration_<reason>` to stderr and exit 2 for user-correctable reasons
(`confirmation_required|preview_stale|foreign_entry_present`) or 20 for environment failures
(`harness_unavailable|timeout|parse_failed|registration_failed`).

## Errors and edge cases

- The short `--set` surface has the sole explicit `--api-key VALUE` exception authorized by
  ADR-012. It warns about shell-history/process-list exposure, never echoes the value, and passes
  it only through mutable confidential-ceremony buffers. Repeating the same command replaces the
  exact stored profile credential through generation-CAS.
- TTY probing failures degrade to non-interactive behavior; nothing prompts without a TTY.
- Marker write failures (unsafe path, I/O error) report `marker_written: false` without
  failing the run.
- Executable paths appear only in the local human/JSON report on the user's own terminal
  (`local_human_view`); they never enter diagnostics or exceptions.
- Automatic discovery considers the reviewed PATH-visible executable names `codex` and
  `codex-testing` only. Other custom wrappers remain selectable through explicit `--codex-path`;
  discovery never executes wildcard `codex-*` programs merely because their names share a prefix,
  and specifically excludes the mutating maintenance helper `codex-testing-update`.

## Invariants

1. Every mutation is preceded by a preview and an explicit acceptance bound to its digest.
2. Service start is the fixed argument-free on-demand launcher. Vault/provider secrets remain
   confined to the existing confidential hidden-TTY helper.
3. A dry run mutates nothing: no marker, no registration.
4. Foreign same-name MCP entries are preserved under every path.

## Tests

- `tests/subprocess/test_setup_wizard_cli.py` — dry run, accept-and-register with marker,
  foreign preservation, multi-candidate fail-closed, no-codex guidance, read-only status, the
  `integrate mcp` matrix, secret-option absence, non-TTY help fallback, and the `--set` success
  path's layer-separated provider readiness wording (no “ready to use this provider” overclaim).
- `tests/conformance/surfaces/test_cli_contract_matrix.py` — `setup` in the frozen command
  matrix and the bare-invocation help regression.

## Open questions

None.

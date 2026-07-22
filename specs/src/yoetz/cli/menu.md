# src/yoetz/cli/menu.py — interactive control menu over the existing command tree

**Wave:** D/F | **ADRs:** ADR-008, ADR-009, ADR-012, ADR-013 | **Imports (spec-tree):**
`specs/src/yoetz/cli/setup.py.md`, `specs/src/yoetz/cli/unlock.md`,
`specs/src/yoetz/adapters/integrations/codex_discovery.py.md`,
`specs/src/yoetz/adapters/integrations/codex_mcp.py.md`,
`specs/src/yoetz/application/harness_mcp.py.md`, `specs/src/yoetz/service/client.md` |
**Imported by:** `specs/src/yoetz/cli/app.md`

## Purpose

Owns the ADR-013 interactive menu: one navigable terminal screen for the operations users
otherwise assemble from subcommands — harness MCP registration, provider-credential ceremonies,
privacy posture inspection, and service lifecycle. The menu is a dispatcher over existing
operations and adds no authority, no new trust boundary, and no new claim vocabulary.

## Public surface

- `menu_available() -> bool` — true only when stdin and stdout are both real TTYs; probing
  failures (`OSError`/`ValueError`) report `False`.
- `run_menu() -> int` — drives the menu loop until quit; on a non-TTY prints a bounded
  `invalid_request` usage message to stderr and returns 2 without prompting.

## Behavior

`run_menu` gathers a bounded overview (service reachability and vault mode via one
`build_service_client().service_status()` probe; per-binary Codex discovery plus registration
state via `HarnessMcpService`+`CodexMcpAdapter`; setup-marker presence via
`setup_marker_present`) and renders a home screen with numbered sections: refresh, setup
wizard, harness connection, LLM provider, privacy, service, quit. Each action runs inside its
own `run_async` event-loop bridge and returns to the menu when it completes.

Dispatch is exact reuse: the wizard section calls `run_setup_wizard` interactively; harness
registration calls `integrate_mcp` (`status|preview|install`) so preview → digest-bound confirm
→ verify gating is unchanged; skill actions send the same
`{action, harness: codex, kind: skill}` integration requests the `integrate <harness> skill`
commands send; the provider section first offers Official OpenAI, Fireworks, or custom HTTPS
origin+model (writes the same `config.toml` fields as `yoetz provider endpoint`), then derives the
exact credential target from that configured provider and delegates to
`cli/unlock.set_provider_credential|rotate_provider_credential`
— the secret is read only inside the existing confidential ceremony; the privacy section performs
read-only
`privacy_get_effective`/`privacy_get_setup` calls and names (never runs) the explicit policy
mutation commands; the service section uses `service_status`/`lock`/`stop` client calls, gates
`stop` behind an interactive confirm, and reproduces the `service unlock` vault-mode dispatch
(`os_keyring` → keyring retry ceremony, `passphrase` → unlock ceremony, uninitialized →
guidance toward passphrase initialization).

Failures are bounded and keep the menu open: `ControlError` renders the same guidance strings
as `cli/app.md` (`vault_locked`/`service_unavailable`/generic); ceremony
cancellation/interruption renders `cancelled`; invalid ceremony previews/results render the
fixed `internal_error` line; invalid identifier input renders `invalid_request`. Results render
as pretty-printed JSON projections of the same values the commands emit (local human view
only). Quit, Ctrl-C, and EOF at a menu prompt exit the loop with return value 0.

## Errors and edge cases

- No secret-shaped prompt exists; credential and passphrase bytes are read only by
  `cli/unlock.py` on the controlling TTY.
- Non-TTY invocation never prompts and never hangs; it is a usage failure (exit 2).
- An unreachable service degrades the overview to an honest "not reachable" line naming
  `yoetz service run`; the menu never spawns the service.
- Adapter/discovery failures degrade to bounded per-binary "unknown (reason)" lines.
- Executable paths and versions appear only in the local human view, never in diagnostics.

## Invariants

1. Every mutation reachable from the menu keeps the identical preview/confirm or ceremony gate
   of its command-tree equivalent; the menu introduces no bypass.
2. The menu never spawns the service and never automates a trusted ceremony or privacy policy
   mutation.
3. No secret enters a menu prompt, argv, env, or output.
4. Menu availability is exactly the two-TTY condition; automation surfaces are unaffected.

## Tests

- `tests/subprocess/test_cli_menu.py` — non-TTY `menu` usage failure, TTY home-screen render
  and quit, section navigation, unreachable-service overview line, and bare-invocation
  dispatch (marker present → menu; non-TTY → help).

## Open questions

None.

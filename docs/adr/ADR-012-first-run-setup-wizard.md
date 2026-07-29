# ADR-012 — First-run setup wizard, automated MCP registration, and the npm launcher

**Status:** Working decision (2026-07-22), founder-authorized amendment of ADR-007 decisions 3, 7,
and 9. Release ratification still requires the packaging evidence gates those decisions already
carry; nothing here manufactures platform or capability evidence.
**Implemented by:** `src/yoetz/ports/harness_mcp.py`,
`src/yoetz/application/harness_mcp.py`,
`src/yoetz/adapters/integrations/codex_discovery.py`,
`src/yoetz/adapters/integrations/codex_mcp.py`, `src/yoetz/cli/setup.py`,
`support/npm-launcher/package.json`, `support/npm-launcher/bin/yoetz.js`,
`support/npm-launcher/README.md`, plus the amended `src/yoetz/cli/app.py`,
`src/yoetz/config/paths.py`, and `docs/runbooks/codex-integration.md`.
**Relates to:** ADR-005 (Codex capability identity), ADR-007 (packaging/release), ADR-009
(privacy/egress), ADR-010 (harness integration port).

## Context

A fresh `uvx yoetz` landed a new user in front of a help screen and a runbook: find Codex, run
`codex mcp get`/`codex mcp add` by hand, start the service, run privacy setup, then the
credential ceremony. Every one of those steps was correct and deliberately manual, but nothing
connected them, and the npm ecosystem had no path at all — ADR-007 deferred `npx yoetz` until it
had "its own provenance, Python/uv delegation, upgrade, and platform contract". This ADR supplies
exactly those contracts and connects the steps without weakening any existing trust boundary.

## Decisions

1. **`yoetz setup` is a new top-level support sub-app** with `run` (the wizard) and `status`
   (read-only posture). The wizard orchestrates only operations a local human could already run by
   hand: Codex discovery, the runbook's check-then-add MCP registration behind
   preview→confirm→execute, a service reachability check, and the existing privacy and
   provider-credential ceremonies. Founder-authorized amendment (2026-07-29): before registration,
   interactive first run chooses a complete `local_only` path or a semantic-review path. The latter
   registers the policy route, configures the provider and credential, asks all thirteen privacy
   questions, renders the exact bounded disclosure, proposes it, and hands widening to the
   separately reauthenticated trusted decision ceremony. On a real local TTY it may invoke the
   already-reviewed hidden-input vault initialize/unlock and credential ceremony; it adds no
   secret field to wizard arguments, configuration, reports, MCP, or agent context. Noninteractive
   setup remains a report plus explicit follow-up commands and never chooses egress. The semantic
   first-run privacy menu suggests `metadata_only`: structural context only, with a foreground
   confirmation before every provider request. It is a starting draft, not consent or a
   provider-data-use recommendation. `private` remains the fail-safe no-egress choice, while
   assisted, expanded, and custom policies remain explicit.

2. **Bounded bare-invocation change (amends ADR-007 decision 3).** The root Typer app drops
   `no_args_is_help=True`; the root callback reproduces the historical help output for every bare
   invocation except one case: stdin and stdout are both real TTYs **and** the completion marker
   (`state_dir()/setup-wizard.json`, schema `yoetz.setup-wizard-marker/1`) is absent. Only then
   does bare `yoetz` launch the interactive wizard. Non-TTY, CI, piped, `--help`, and every named
   subcommand invocation are byte-for-byte unchanged. The marker is permanent once a mutating
   wizard run completes — a decline counts as completion; re-runs happen only via explicit
   `yoetz setup run`. An unsafe or unreadable state directory never triggers the wizard.

3. **MCP registration becomes a first-class preview-gated operation** (`yoetz integrate <harness>
   mcp status|preview|install`), automating the exact two-command sequence the Codex runbook
   already mandates: `codex mcp get yoetz --json` first; `codex mcp add yoetz -- yoetz mcp serve`
   only when no entry exists; a foreign same-name entry is preserved and refused with
   `foreign_entry_present` — there is no force path. Success is verified by re-reading state, not
   by trusting the add exit code. Registration remains a fact separate from skill installation and
   from Codex capability support (E-002/E-013 are untouched); "registered" never implies "Codex
   will successfully connect".

The short `yoetz --set --fireworks --model MODEL` and `yoetz --set --grok --model MODEL` paths are
provider-only entries into the same setup ceremonies. They derive internal provider bindings and
always collect the API key through hidden TTY
input. Credential-valued command arguments are not accepted, so noninteractive setup cannot bypass
the local confidential ceremony. Repeating the same command updates the exact stored profile
credential through generation-CAS.

**Amended 2026-07-28 — deterministic model suggestions.** Every reviewed provider preset carries a
repository-owned, default-first model suggestion tuple capped at ten entries. Interactive
`yoetz provider endpoint` selectors, the endpoint menu, and the provider-only `yoetz --set` paths
use the same numbered picker and always show a custom model-ID entry. An explicit `--model` bypasses
the picker unchanged. Owner-declared endpoints remain manual because the repository cannot know
their model namespace. The picker performs no provider request: CLI/setup code owns no outbound
provider channel or credential, and ADR-006/009 require actual dispatch to remain behind the
service privacy gateway. Catalog entries are reviewed convenience metadata, not proof of account
availability, structured-output interoperability, provider data use, or E-007 capability.

4. **A sibling port, not an `IntegrationsPort` extension (amends ADR-010 by addition only).**
   `HarnessMcpPort` (`ports/harness_mcp.py`) owns registration with its own closed types
   (`HarnessBinary`, `McpRegistrationState/Action/Reason`, digest-bound preview/command/result).
   Skill install types carry trusted-project file semantics (`project_root`, `file_changes`,
   managed markers) that registration must not reuse. ADR-010's guarantee is preserved: adding a
   harness is still one `HarnessId` value plus adapters, with no port or registry change.

5. **Discovery is pure observation.** `discover_codex_binaries` scans `$PATH` plus reviewed app
   locations: the standard macOS Codex Desktop resource directory and the resource directory from
   the Windows Store package family `OpenAI.Codex_2p2nqsd0c76g0`, resolved by a bounded read-only
   package query. Linux has no official Codex App distribution today, so no app path is fabricated.
   Exact allowlisted names are `codex` and `codex-testing` on POSIX, with `.exe`/`.cmd` forms on
   Windows. Results are deduped by resolved target while keeping the visible candidate path, then
   version-probed
   `codex --version` with a bounded timeout, and always reports `untested` compatibility (E-002:
   a version string is not support evidence). Interactive setup first presents the automatically
   detected supported harnesses — exactly **Codex** in v0.1 — as a numbered choice, then presents a
   separate numbered installation choice when several Codex executables exist. Human-facing copy
   uses the brand names **Yoetz** and **Codex**; the executable, command, and MCP server identifiers
   remain the protocol-owned lowercase `yoetz`/`codex` tokens. Non-interactive runs fail closed on
   multiple installations and require `--codex-path`. Every registration preview requires an
   explicit `Y` or `N` answer with no implicit default; `--accept` remains the explicit automation
   path. Discovery never widens to `codex-*`: in particular, `codex-testing-update` is not executed
   or presented as an installation. macOS and Windows therefore combine app and CLI installations;
   Linux uses the identical selection flow for the official CLI surfaces that actually exist.

6. **The npm launcher exists, publish-ready and deliberately unpublished (amends ADR-007
   decision 7).** `support/npm-launcher/` contains a dependency-free `package.json` (registry name
   `yoetz`, version locked to the PyPI version) and `bin/yoetz.js`, which requires `uv` on PATH
   (printing install guidance and exiting nonzero otherwise) and delegates to
   `uvx yoetz==<version>` with untouched arguments and the child's exact exit code. It bundles no
   Python, downloads nothing itself, and duplicates no wizard logic — first-run behavior lives
   once, in the Python CLI. `"private": true` is the load-bearing unpublished guarantee; flipping
   it is a separate deliberate release decision with its own review, never a side effect.

7. **The confidential boundaries remain exact.** The wizard never accepts a secret by flag,
   ordinary stdin, environment, config, report, or MCP. A local interactive run may enter the
   existing confidential helper, which reads vault and provider secrets with hidden `/dev/tty`
   input and sends them only over YZS1. Noninteractive setup never provisions a credential.
   Human setup and provider-status output renders only a constant `********` presence mask after the
   trusted service confirms the configured profile has a credential; confirmed absence and
   unreadable state remain distinct. The mask never reflects secret bytes or secret length. A
   repeated setup run recomposes the service after binding, observes that exact profile, and skips
   credential entry when presence is already confirmed. If a credential write commits but its
   result frame is lost, setup recomposes and recovers only from the configured profile's trusted
   presence bit; an unreadable or absent result remains failed rather than being inferred as
   success.

8. **Founder-authorized on-demand service start (2026-07-22 amendment).** A mutating interactive
   setup run and the MCP bridge may invoke the shared fixed-command service launcher when the
   authenticated endpoint is absent. The launcher executes only the current installed
   `python -m yoetz service run`, supplies no caller path/config/provider/secret argument, strips
   secret-shaped inherited environment names, detaches using the reviewed platform process flags,
   and reconnects to the singleton winner. The service stops after 1,800 seconds of true
   quiescence; a later MCP tool call may start a generation-fenced successor. A locked successor
   remains locked and still requires local-human unlock.

## Consequences

A new user's path is now: `npx yoetz` or `uvx yoetz` → interactive wizard → detected-harness
selection (Codex in v0.1) → installation selection when needed → explicit `Y`/`N` confirmation →
local-only or semantic-review choice → route-matched Codex MCP registration → on-demand local
service → local vault/provider ceremonies when semantic was chosen → thirteen-answer privacy
review → separately reauthenticated privacy decision.
Each mutating step is previewed, digest-bound, and individually declinable; `yoetz setup status`
reports the same posture read-only at any time. The CLI support-command matrix grows by one
(`setup`), recorded in the conformance contract test in the same change.

The cost is one bounded exception to the previously uniform bare-invocation behavior, and a second
distribution surface to keep in version lockstep — enforced by a packaging test that compares the
npm launcher version to the Python package version.

## Alternatives considered

**Reuse `IntegrationsPort` for registration.** Rejected: the skill types' project-root and
file-inventory fields would be dead or misleading for a global registration, and overloading them
would weaken the ADR-010 fork guarantee.

**Trigger the wizard from the npm bootstrap script.** Rejected: it cannot serve `uvx yoetz`
users and would duplicate first-run logic in two languages with drift risk.

**Offer a force-replace for foreign MCP entries.** Rejected for v0.1: the runbook's
preserve-and-review rule stands; a foreign entry is reported with a manual follow-up instead.

**A `YOETZ_SKIP_SETUP_WIZARD` environment opt-out.** Rejected: the non-TTY guard already covers
automation, and the marker covers humans; an ambient env escape would make first-run silently
skippable by inherited shell configuration.

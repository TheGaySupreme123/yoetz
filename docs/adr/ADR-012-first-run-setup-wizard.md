# ADR-012 — First-run setup wizard, automated MCP registration, and the npm launcher

**Status:** Working decision (2026-07-22), founder-authorized amendment of ADR-007 decisions 3, 7,
and 9. Release ratification still requires the packaging evidence gates those decisions already
carry; nothing here manufactures platform or capability evidence.
**Implemented by:** `src/yoetz/ports/harness_mcp.py`,
`src/yoetz/application/harness_mcp.py`,
`src/yoetz/adapters/integrations/codex_discovery.py`,
`src/yoetz/adapters/integrations/codex_mcp.py`,
`src/yoetz/adapters/integrations/codex_marketplace.py`, `src/yoetz/cli/setup.py`,
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
   provider-credential ceremonies. Founder-authorized amendment (2026-08-04): before registration,
   interactive first run chooses a complete `local_only` path or a semantic-review path.
   Founder-authorized amendment (2026-08-05): the semantic-review path is offered first and is the
   pre-selected answer in both the prompt-loop wizard and the terminal interface. This is a
   property of that question only. It changes no seeded state: the durable policy an installation
   starts with is still `local_only`, the answer binds no provider, stores no credential, and
   commits no policy, and local-only remains one keystroke away. The reason for the
   recommendation is that an installation that never reaches the semantic path can only ever
   report deterministic coverage, and a first-run default that quietly guarantees that outcome
   under-serves the operator who came for review. The same amendment requires that the answer be
   taken *before* the MCP registration it determines, and that the registered route follow from it
   — registering first and asking after is how a semantic install ends up ceilinged on the strict
   route. The latter path
   registers the policy route, configures the provider and credential, then renders one exact
   recommended `assisted_review` policy only when the exact provider route has current reviewed
   no-training evidence and retention no longer than 30 days; otherwise it recommends `private`.
   Accepting that exact draft asks nothing further; declining
   it opens the named recipes, which materialize directly into a draft, with only `custom`
   reaching field-level configuration in five grouped sections. Both paths propose
   the reviewed exact disclosure and hand widening to the separately reauthenticated trusted
   decision ceremony. On a real local TTY it may invoke the
   already-reviewed hidden-input vault initialize/unlock and credential ceremony; it adds no
   secret field to wizard arguments, configuration, reports, MCP, or agent context. Noninteractive
   setup remains a report plus explicit follow-up commands and never chooses egress. The semantic
   `assisted_review` is repository-scoped, problem-local, and does not require recurring prompts
   after its trusted policy commit. Unknown, stale, broad, or account-unqualified evidence never
   earns the recommendation and remains an informed explicit choice only. It is a starting draft,
   not consent. `private` remains the fail-safe no-egress choice, while Metadata only, expanded,
   and custom policies remain explicit. `yoetz --privacy` enters
   the same short recommended-first ceremony at any later time. **Amendment (issue #164,
   2026-08-09):** when a user asks a capable first-party agent for help finishing exact provider
   credential or repository privacy setup, the agent may relay an explicit current-chat instruction
   through `yoetz consent authorize` after one warning without requiring a second local-terminal
   ceremony. Yoetz binds the exact action but cannot independently authenticate the chat provenance;
   the trusted CLI/TUI remains recommended and always available. The setup update advisory's version
   parser is a declared, exactly pinned core dependency; a clean installed-artifact gate imports
   and enters `setup run` so development-only transitive packages cannot hide a missing runtime
   dependency. **Amendment (issues #204 and #205, 2026-08-12):** first-run setup asks whether the
   proposed privacy policy should enable the structural PyPI update check (default yes). It carries
   that boolean into the recommended or named recipe before rendering the exact candidate; it
   changes no other recipe field, creates no authority itself, and never bypasses candidate
   confirmation or the existing trusted service decision ceremony. Declining produces the genuine
   zero-network `private` candidate rather than silently restoring the recipe default.

   Repository scope comes only from the service's trusted locator path. CLI and TUI supply their
   actual working directory; MCP supplies its configured/session working directory. The service
   resolves the canonical Git common root (or resolved non-Git directory), commits it under the
   installation key, and discards the raw path. Public `workspace_ref` remains a task-attachment
   selector and cannot select privacy authority. A proposal for a new repository may combine a
   necessary machine-ceiling widening with insertion of the first repository row; setup renders the
   complete two-part change and binds one approval to one authority digest and atomic CAS.

   Upgrades preserve previously accepted machine-policy bytes. Eligible pre-upgrade routes consume
   bounded migration entitlements when their trusted repository locator next arrives; if none existed,
   one bounded first-repository carry-forward is available. This automatic step only narrows existing
   authority and does not ask again. New repositories beyond those entitlements remain Private.

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

   **Founder-authorized Codex activation repair (2026-08-03).** An accepted setup now also installs
   the packaged project skill at `.agents/skills/yoetz` before it installs the structural plugin
   sources and registers MCP. A directory at `.agents/plugins/yoetz` is not reported as Codex
   plugin activation: current Codex requires marketplace registration plus an explicit plugin add,
   and setup does not silently mutate that global trust/configuration surface. The plugin directory
   remains a managed source bundle for guidance and hook definitions; the project skill is the
   discoverable project-local activation cue. On an unprofiled Codex release, setup may install the
   byte-exact reviewed skill after the enclosing digest-bound project approval, but reports
   compatibility as `unsupported` and automatic activation as untested. The standalone
   `integrate skill install` command retains its exact capability gate.

   **Amended 2026-08-12 — consent-based Codex plugin activation (issues #204 and #205).** Setup
   may now offer the additional Codex activation needed for installed hook sources to run, but the
   live-smoke correction is load-bearing: repository marketplace bytes plus a plugin-enable TOML
   block do **not** prove activation. Codex also requires the plugin in the canonical inventory and
   loads hooks from its versioned plugin cache.

   Activation is bound to the exact executable selected during discovery and an existing absolute,
   non-symlink Codex home explicitly supplied by the owner end to end. It never derives that home
   from a basename, wrapper behavior, ambient environment, or pre-consent diagnostic. Before
   consent, preview runs only that executable's `--version`, forcing both `CODEX_HOME` and
   `CODEX_TESTING_HOME` to a fresh owner-private temporary home. Codex may create scratch even for
   its version command, so setup removes that temporary home afterward. It performs no canonical
   inventory read against the selected home before approval, preserving first-run zero-egress and
   no-selected-home-mutation truth.

   The preview digest binds the selected executable path and SHA-256, parsed version, explicit home,
   exact repository marketplace before/after bytes, exact config presence/preimage and append-only
   after bytes, managed source-tree digest, versioned cache target and preimage, intended cache
   digest, temporary-private-home probe environment, forced selected-home activation environment,
   and the exact post-consent `plugin list --marketplace yoetz --json` and
   `plugin add yoetz@yoetz --json` commands. Setup displays the bounded targets, environments,
   commands, digests, resulting blocks, possible scratch/cache/config/marketplace effects, and
   standing-trust warning before asking for explicit approval.

   After consent, apply forces both home variables to the approved home, takes an owner-only
   activation lock, re-probes the exact executable and bound preimages, refuses stale or foreign
   state, preserves unrelated marketplace entries and config text, CAS-fences each write, and runs
   the scoped inventory/add commands through that selected executable. A failure after a write or
   add preserves already-approved marketplace/config/cache partial state and reports failure for an
   honest retry. It performs no pathname rollback: a verify-then-delete or overwrite could race a
   concurrent replacement. Post-apply inspection calls the canonical inventory and reports
   `active` only when the managed source is installed, repository marketplace and selected-home
   configuration are exact, inventory says `yoetz@yoetz` is installed and enabled from this
   repository, and the inventory's versioned cache is byte-identical to the managed plugin tree.
   Otherwise the closed state is
   `installed_not_activated`, `not_installed`, or `foreign`; installed bytes, configuration, cache,
   and inventory remain separately reported facts. Declining changes none of them. This ceremony
   authorizes a standing Codex trust change for future sessions in that exact Codex home; it does
   not prove that a later session loaded a hook or delivered an observation.

   **Amended 2026-08-15 — version-gated async observation hooks (issue #271).** Codex releases
   before `0.148.0-alpha.6` recognize `"async": true` but discard non-`SessionEnd` command
   handlers that declare it; they do not downgrade those handlers to synchronous execution. The
   managed plugin renderer therefore emits async pure-ingress handlers only when the exact probed
   Codex version parses as SemVer/PEP 440 and is at least `0.148.0-alpha.6`. Missing, malformed,
   oversized, and older versions fail closed to the bounded synchronous form so observation is
   slower rather than absent. Advice-bearing handlers and `SessionEnd` remain synchronous on every
   version.

   The selected version also binds the rendered source marker, activation preview's managed-source
   and intended-cache digests, and every apply-time source re-render. Setup may preview a transition
   from either byte-exact managed hook variant, but apply requires the intended variant before its
   first mutation; arbitrary or modified trees remain refused. Crossing the capability boundary
   consequently requires the ordinary digest-bound plugin refresh and activation approval. The
   committed/unprobed plugin tree is the conservative synchronous variant.

   **Amended 2026-08-21 — canonical source, host-rendered cache, and managed refresh (issues
   #387 and #388).** Live testing on an async-capable host showed the previous amendment's
   version-threaded *source* render deadlocks activation: the committed project tree deliberately
   carries the canonical async-free render (pinned by packaging tests), so comparing it against
   the host-specific render fails `modified_copy` forever and `inspect_activation` reads
   `not_installed` with everything else in place. The corrected split is: the project-tree plugin
   source is always the canonical (`codex_version=None`) render — the wizard's project-source
   preview/inspect/install never thread the probed version, and the committed tree stays
   byte-stable — while host-specific rendering belongs only to the activation cache layer. The
   probe version selects the intended cache bytes; `plugin add` copies the canonical source, and
   apply then atomically replaces that marker-identified copy with the exact previewed
   host-rendered bytes and verifies the bound install digest. A source tree that reads as
   `installed` is a byte-exact canonical or host-variant render; a tree that instead byte-matches
   its own valid `yoetz.codex-plugin-install/1` marker (a prior managed render, including the
   async-variant or an older-guidance form) is replaceable by install without `replace_modified`,
   while genuinely modified trees keep the `modified_copy` refusal.

   The same marker rule repairs same-version cache refresh: package version `0.1.0` is stable
   while plugin content drifts, so a previously activated home's versioned cache can differ from
   the fresh render without being foreign. A cache tree carrying a valid install marker that
   byte-matches its own inventory is classified replaceable — preview binds its digest as the
   cache preimage, apply atomically swaps the whole directory, and a cache changed between preview
   and apply still refuses as `preview_stale`. `destination_conflict` remains reserved for
   foreign, marker-inconsistent, or modified cache trees. `active` now requires the versioned
   cache to match the host-specific render rather than the managed source bytes.

   **Amended 2026-08-21 — explicit route input and honest activation reporting (issues #389 and
   #390).** `setup run` and `integrate <harness> mcp preview|install` accept an explicit
   `--route-profile strict|policy`. Without that input, an existing yoetz-owned registration keeps
   its observed route — the structural configuration derivation (including its fail-closed
   exception fallback) applies only to a fresh registration, and non-interactive `--accept` alone
   never changes an existing route. The interactive wizard still derives the route from the
   review-mode answer, and any transition of an existing owned route is shown in the confirmed
   preview and reported as `route_profile_before` → `route_profile`. Separately, when an explicit
   Codex home was supplied, the registration and readiness `plugin_activation` blocks echo the
   bound home/config path and the actual activation failure reason instead of resetting to
   `codex_home_required`, and unobserved readiness facts are reported as null rather than asserted
   `false`.

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

6. **The npm launcher is a protected public distribution surface (amends ADR-007 decision 7).**
   `support/npm-launcher/` contains a dependency-free `package.json` (registry name
   `yoetz`, version locked to the PyPI version) and `bin/yoetz.js`, which requires `uv` on PATH
   (printing install guidance and exiting nonzero otherwise) and delegates to
   `uvx yoetz==<version>` with untouched arguments and the child's exact exit code. It bundles no
   Python, downloads nothing itself, and duplicates no wizard logic — first-run behavior lives
   once, in the Python CLI. The separate deliberate release decision was made for v0.1.0 on
   2026-08-20 (issue #366): the tagged workflow publishes the exact prebuilt npm tarball only after
   matching PyPI publication, using npm trusted publishing and post-publication byte verification.

7. **The confidential boundaries remain exact.** The wizard never accepts a secret by flag,
   ordinary stdin, environment, config, report, or MCP. A local interactive run may enter the
   existing confidential helper, which reads vault and provider secrets with hidden `/dev/tty`
   input and sends them only over YZS1. Noninteractive setup never provisions a credential.
   Human setup and provider-status output renders only a constant `********` presence mask after the
   trusted service confirms the configured profile has a credential; confirmed absence and
   unreadable state remain distinct. The mask never reflects secret bytes or secret length. A
   repeated setup run recomposes the service after binding and observes that exact profile. When
   presence is confirmed, it asks whether to reuse the stored credential (default) or replace it
   through the same hidden-input ceremony. If an initial credential write commits but its result
   frame is lost, setup may recover only from the configured profile's trusted presence bit. A
   replacement started with presence already true cannot use that bit as proof that the new value
   committed; an ambiguous replacement remains failed instead of inheriting the old value's
   presence as success.

8. **Founder-authorized on-demand service start (2026-07-22 amendment).** A mutating interactive
   setup run and the MCP bridge may invoke the shared fixed-command service launcher when the
   authenticated endpoint is absent. The launcher executes only the current installed
   `python -m yoetz service run`, supplies no caller path/config/provider/secret argument, strips
   secret-shaped inherited environment names, detaches using the reviewed platform process flags,
   and reconnects to the singleton winner. The service stops after 7,200 seconds of true
   quiescence; a later MCP tool call may start a generation-fenced successor. A locked successor
   remains locked and still requires local-human unlock.

## Consequences

A new user's path is now: `npx yoetz` or `uvx yoetz` → interactive wizard → detected-harness
selection (Codex in v0.1) → installation selection when needed → explicit `Y`/`N` confirmation →
local-only or semantic-review choice → discoverable project skill → structural plugin/hook sources
→ route-matched Codex MCP registration → on-demand local service → trusted repository binding →
local vault/provider ceremonies
when semantic was chosen → recommendation-first privacy review → separately reauthenticated privacy
decision.
Each mutating step is previewed, digest-bound, and individually declinable; `yoetz setup status`
reports the same posture read-only at any time. The CLI support-command matrix grows by one
(`setup`), recorded in the conformance contract test in the same change.

**Amendment (ADR-023, 2026-08-21, issue #149): host-derived artifact projection behind the
unchanged wizard.** The setup ordering and user experience above do not change: project skill,
then structural plugin sources, then explicitly approved activation, then MCP verification, each
previewed, digest-bound, and individually declinable. What changes is backend selection only: the
structural plugin-source step becomes a projection of the neutral `PortablePluginPlan`, chosen
from the detected host profile — the portable Agent Plugins artifact in the host's own client
plugin root where the host supports it, a generated native projection in that host's distinct
documented native root where it does not. The user never chooses a root, format, or migration
path. Artifact preview/apply, host activation, MCP registration, and observation consent remain
separately reported facts, and setup status additionally reports the closed `McpOwnershipState`
(`absent|external|plugin|dual|foreign|ambiguous`): dual, foreign, and ambiguous ownership are
explicit reported states that setup never silently resolves or overwrites. For Codex the plugin
root stays `.agents/plugins/yoetz`; a format migration there is a whole-directory,
marker-identified, digest-bound replacement under this ADR's existing preview/apply and activation
ceremony, and the bespoke layout remains the shipping control until a portable projection is
capability-proven and explicitly approved. This amendment grants no new authority: the wizard's
narrow approval covers exactly what it covered before, and the standalone portable
install/remove/activation-apply paths, once implemented by #150, take the ADR-016 `review_only`
lane instead (ADR-023 decision 11).

Issue #150 implements portable artifact render/preview/status and the exact
`plugin_artifact_apply` review-only consumer. The shipping wizard still selects the native Codex
control until a later capability cell proves portable discovery and activation. A portable
preview therefore creates no activation claim: status keeps rendered bytes, installed bytes, MCP
ownership, discovery, activation, and skill delivery separate.

Issue #151 adds the optional plugin-managed backend without changing this authority order. Setup
must select `external_registration` or `plugin_managed`, and for the latter select `strict` or
`policy`, before rendering the preview. The preview shows and binds the current ownership state,
exact route argv, schema/renderer version, and complete bytes. `dual`, `foreign`, `ambiguous`, or a
changed owner refuses apply without overwrite. Activation follows artifact apply and never counts
as privacy/provider consent. Rollback removes only exact managed bytes (restoring the retained exact
native tree when present); it preserves foreign host config and all Yoetz durable state. PATH or
executable failure is a capability diagnostic after activation, never permission to inject shell
configuration, environment values, or credentials.

Package replacement changes binaries, not accepted trust bytes. Installed-wheel proof is still
required before issue #139 can close: consecutive real checks must prove distinct attempt authority
and receipts in one approved repository, and a second repository must remain blocked. Router routing
and issue #141's foreground disclosure continuation are outside this wizard change.

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

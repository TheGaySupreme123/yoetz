# Codex integration runbook

This runbook guides you through previewing, installing, checking, replacing, and removing the
canonical Yoetz Codex skill in one explicitly trusted project, while preserving any files you have
modified. It also separates four facts that are easy to conflate: skill/source installation, Codex
plugin activation, MCP registration, and Codex's tested capability for a given Codex version.

## 1. What integration installs — and does not

The standalone `yoetz integrate skill` surface installs only the canonical `SKILL.md`, its named
references/compatibility data, and a nonsecret managed marker, at exactly:

```text
<explicit-trusted-project>/.agents/skills/yoetz/
```

It does **not** edit Codex's global or project configuration, register or start the MCP server,
install or update Yoetz or Codex, touch your Git index/ignore/remote/branch, scan your repository
contents, install globally, manage any other skill, or make Yoetz mandatory for your project. The
project root must be explicitly supplied and trusted by you — there is no current-directory,
parent-directory, fuzzy discovery, or symlink-target resolution.

The installed files are byte-identical to the packaged wheel resource. The managed marker records
only versions and sorted digests — never a path, username, timestamp, or repository reference.

ADR-023 (issue #149) accepts a portable Agent Plugins carrier design. Issue #150 implements its
skills-only renderer and whole-directory migration/rollback at the same
`.agents/plugins/yoetz` root; see the
[portable plugin authoring runbook](portable-plugin-authoring.md). Until that projection is
capability-proven and explicitly approved by a later host cell, the native behavior in this
runbook remains the shipping control and nothing here changes.

Issue #151 adds an optional generated plugin-managed MCP mode to that portable artifact. It is not
the default rollout and does not change the native control. If selected by a later proven host
cell, preflight must show no external/global `yoetz` registration, bind strict or policy before
preview, and install the full digest-bound artifact without invoking `codex mcp add`. Dual,
foreign, or unobservable ownership stops the operation. `yoetz provider status --json` reports
`owner_source`, `ownership_state`, and the observed route profile; only one exclusively observed
policy owner can make `agent_route_semantic_ready` true.

## 2. Prerequisites and exact supported scope

Check `yoetz version --json`, the installed resource set, and the current compatibility/capability
matrix. Confirm owner permissions on the target project, that you trust this repository, and the
expected Codex version. Codex support is the exact tested set in the packaged manifest; an empty
set means no Codex release currently carries automatic-activation support evidence. A version
string or successful file install never promotes an unprofiled release to supported.

## 3. Status and preview

Always run status first:

```text
yoetz integrate codex skill status --json
```

Destination states: `absent`, `installed_exact`, `modified`/`unmanaged`, `partial`, `unsafe`.
Compatibility is reported separately as `supported`, `unsupported`, or `untested`.
A symlink at `.agents` or `.agents/skills` is `target_unsafe` and is never followed (issue #396).
Status is read-only — it never repairs or updates anything. An identical directory without a valid
managed marker is treated as unmanaged/modified and is protected from removal. `installed_exact`
does **not** by itself prove Codex has discovered the skill or that MCP is available.

## 4. First install

```text
yoetz integrate codex skill preview --json
yoetz integrate codex skill install --json
```

1. Preview shows the fixed destination and scope, the source skill/protocol/resource/Codex-tested
   identities, the current state, the create/replace/no-op file digest and size changes, warnings,
   and a preview digest.
2. For an absent target, explicitly confirm the exact preview digest to install. Non-interactive use
   requires the acceptance flag plus the plan digest — there is no implicit prompt or hang.
3. An already-`installed_exact` target returns a no-op. An incompatible or unsafe target stops
   without a force option.
4. If a timeout or cancellation happens around the file swap, run `status` again with the same
   target before doing anything else — do not start a new preview until you know whether the old,
   new, or a partial state is on disk.

## 5. Upgrade or replace a modified or partial copy

Default install **never overwrites**. If your copy is modified or partial, inspect and retain it
using your normal source control or a manual copy first. If you deliberately want the packaged
source to replace it, request `replace_modified` before preview, review the exact current
digest/diff, and confirm that exact preview — a generic `--yes` is not sufficient. Any concurrent
edit makes the preview stale and preserves your files untouched.

Replacement stages and swaps the **whole directory**, never merging individual files. An
interrupted, ambiguous old/new state remains preserved and requires a fresh `status`/preview and
manual review — never delete staged or rollback content blindly.

## 6. Verify Codex discovery and Yoetz availability

After an exact install, launch the exact tested Codex version in this trusted project and
explicitly invoke `$yoetz` (implicit discovery is not assumed unless the current capability matrix
advertises it). Confirm the skill is recognized, its version is compatible, and the workflow
guidance appears. **Installation alone is not evidence of discovery.**

## 7. Optional versus required MCP behavior

MCP registration is a separate step from skill installation:

```text
codex mcp get yoetz --json
codex mcp add yoetz -- yoetz mcp serve
```

Run `codex mcp get yoetz --json` first. A nonzero result does not prove absence: Yoetz follows it
with `codex mcp list --json` and continues only when that command succeeds with no `yoetz` entry.
A failed/malformed list or duplicate matching names fails closed; a single matching entry is
classified by its exact command. Strict parsing also rejects duplicate JSON keys, nonstandard
constants, and truncated output. If an entry already exists, preserve it and stop unless a
separately reviewed operation proves it is the exact Yoetz-owned registration being intentionally
replaced. Current Codex `mcp add` behavior replaces a same-name global entry, so this positive
absence check matters. Codex exposes no compare-and-add token: keep other MCP configuration writers
quiescent during an accepted apply, because Yoetz cannot atomically exclude a non-cooperating write
inside the final subprocess window.

The registration check-then-add flow is available as
`yoetz integrate codex mcp status|preview|install` and is what `yoetz setup run` performs after
Codex discovery (ADR-012). The separate removal check-then-remove flow is
`yoetz integrate codex mcp preview-remove|remove`. Both flows are gated by an explicit
digest-bound confirmation, preserve entries already observed as foreign, and verify the final
state by re-reading it. A "registered" result still never implies Codex will successfully connect
at runtime.

## Auto review and host admission

Under `approval_mode = auto` (the default), Codex needs approval for an MCP call iff
`destructiveHint == true`, else never for `readOnlyHint`, else
`destructive.unwrap_or(true) || open_world.unwrap_or(true)` (`codex-rs/core/src/mcp_tool_call.rs`).
Applied to Yoetz's frozen descriptors, only the policy-route `check` (`openWorldHint: true`)
needs approval; with `approvals_reviewer = "auto_review"` that approval goes to the guardian,
whose bundled policy requires authorization for sensitive egress to name payload and destination
"from trusted user content" — no descriptor wording can satisfy it. `approval_mode = "approve"`
for one tool means the reviewer is never invoked for it; `prompt` forces it every time.

Host admission (issue #467) writes the per-tool override into the trusted project's
`.codex/config.toml`, which Codex loads only when the project is trusted, deep-merges over the
user-level `[mcp_servers.yoetz]` (`codex-rs/config/src/merge.rs`), and which cannot carry
provider or credential keys (`mcp_servers` is not on the project-layer denylist):

```toml
[mcp_servers.yoetz.tools.check]
approval_mode = "approve"
```

or, for a plugin-managed route, `[plugins."yoetz@yoetz".mcp_servers.yoetz.tools.check]`. The
form follows the exclusively observed owner (`yoetz provider status --json`
`mcp_route.ownership_state`):

```text
yoetz integrate codex admission preview --project-root <project> --json
yoetz integrate codex admission grant --project-root <project> --accept --preview-digest <digest>
```

A strict registered route, a missing or non-permitting grant, or an unreadable service refuses
before any write. A same-name table that is not byte-exact (another `approval_mode`, an extra
key) or a server-level `default_tools_approval_mode` is `foreign`: reported, never edited.
An exact table for only the inactive owner does not make the active owner present; grant adds the
applicable table instead of returning a false no-op. Removal strips every exact generated owner
form; a config that held nothing else is deleted.

A mutating preview warns `host_config_not_compare_and_swap`; keep Codex and other settings writers
quiescent during apply. Yoetz rechecks the exact preimage immediately before mutation and verifies
the result, but an ordinary file cannot exclude a non-cooperating same-UID writer in the final
syscall window.

Reverse: `integrate codex mcp install --route-profile strict --project-root <project>` and
`integrate codex mcp remove --project-root <project>` sweep the project's entry and report
`admission_cleanup` (the registration is global and the admission is project-scoped, so without
`--project-root` nothing is swept and `provider status` reports `host_admission_drift` — that
report walks from the launch directory to the repository root, so a subdirectory cwd does not
read as `absent`);
`integrate codex plugin remove` sweeps it for the bound project; a privacy commit that stops
external review sweeps it in the ceremony. The sweep still runs when MCP install/remove is already
a no-op, because the route state and the project admission state are independent.

Codex exposes no typed denial signal for a guardian refusal: its `PermissionRequest` hook fires
before the decision and may allow, so it is not a denial. A held check is visible only as the
#187 pause/approval flow in the transcript. This is a documented gap, not a Yoetz diagnostic.
The 2026-08-30 source read is not a live cell; the `auto_review` acceptance cell in issue #467
remains to be run.

## Upgrading Yoetz under a running service

The local-control handshake pins the exact schema-manifest digest, so after installing a new Yoetz
build the previous build's service still owning the endpoint refuses the new bridge and CLI. The
first MCP tool call (on-demand startup) replaces that service automatically: it asks the stale
holder to shut down through its ordinary bounded path and starts this installation's service inside
the same 30-second budget. If that cannot complete, the tool returns `SERVICE_UNAVAILABLE` with
`reason_code: service_incompatible` (or `protocol_mismatch` when the refusal is a protocol
generation mismatch) and the repair command; run it on a local terminal:

```text
yoetz service restart
```

`yoetz service status` names the incompatible holder's pid, version, and manifest digest. Other
hosts' sessions still running the previous build's bridge are refused after the switch until they
restart; that is the intended outcome of an upgrade, not a defect. The cooperative MCP bridge
latches that availability failure for the process and serializes the first on-demand attempt so
concurrent tool calls share one diagnostic (issues #469, #476); that behaviour is shared across
hosts that use this bridge, not Codex-specific.

The accepted setup path composes four separately reported layers in order: it installs the project
skill at `.agents/skills/yoetz`, installs managed structural plugin/hook sources at
`.agents/plugins/yoetz`, applies an explicitly approved Codex activation, then verifies the MCP
entry. The plugin source directory, marketplace entry, or enabled config table alone is not
evidence that Codex activated a plugin.

Activation is a standing-trust mutation for future sessions in one owner-selected Codex home. The
owner must explicitly supply an existing absolute, non-symlink home; setup never derives it from a
wrapper basename, ambient environment, or a pre-consent Codex diagnostic. Setup binds the exact
selected executable path and SHA-256 and obtains its version by running only `--version` with both
`CODEX_HOME` and `CODEX_TESTING_HOME` redirected to a fresh owner-private temporary home. Codex may
create scratch even for that command, so the temporary home is removed afterward. No selected-home
inventory command runs before approval.

The preview digest also binds the trusted project root, repository marketplace, and selected-home
config preimages/proposals, managed source-tree digest, cache root
`<selected-home>/plugins/cache/yoetz/yoetz`, cache preimage/intended install-tree digest, the
temporary-private-home probe environment, the forced selected-home environment for mutation, and
the exact post-consent commands `plugin list --marketplace yoetz --json` and
`plugin add yoetz@yoetz --json`. Review the displayed targets, environments, commands/digests,
resulting marketplace/config bytes, possible selected-home scratch/cache effects, and warning
before approving; do not substitute a manual `plugin add` for that ceremony.

After consent, apply forces both home variables to the approved home, re-probes bound state under
an owner-only home lock, CAS-fences each write, invokes the exact selected executable for scoped
inventory/add, and validates its reported installed path/version. A later failure preserves any
already-approved marketplace/config/cache partial state for an honest retry; it does not attempt a
pathname rollback that could delete or overwrite a concurrent change. `active` means all of these
agree: managed source installed, repository marketplace and selected-home config exact, canonical
inventory says `yoetz@yoetz` is installed and enabled from this repository, and the installed
version cache is byte-identical to the host-specific render of the managed source. Other closed
states are `installed_not_activated`, `not_installed`, and `foreign`. `not_installed` is source
absence only; a modified or untrusted byte-present tree is `installed_not_activated` and is never
`active` (issue #347). None of them—and not even
`active`—proves a later Codex process loaded a hook or delivered an observation.

The managed project source always carries the canonical async-free render; the host-specific form
(async pure-ingress hooks from Codex `0.148.0-alpha.6`) exists only in the versioned activation
cache, which apply seeds and verifies against the previewed install digest. Because the package
version stays constant while plugin content drifts, a previously activated home's cache can
legitimately differ from the fresh render: a cache tree that carries a valid
`.yoetz-plugin-install.json` marker and byte-matches that marker's own inventory is a prior
yoetz-managed render, previewed as a same-version refresh and replaced atomically on apply.
`destination_conflict` is reserved for foreign, marker-inconsistent, or modified cache trees —
those still require the owner to resolve the conflict by hand.

Registration also decides *which* route the agent gets. Both owned serve commands classify as
`yoetz_owned`, so the state alone cannot tell a strict registration from a policy one. Read the
route from `yoetz integrate codex mcp status --json` (`route_profile`) or from
`yoetz provider status --json` (`mcp_route.registered_profile`). The route is explicit input:
pass `--route-profile strict|policy` to `yoetz setup run` or
`yoetz integrate codex mcp preview|install` to choose it. Without that flag an existing
yoetz-owned registration keeps its current route (non-interactive `--accept` never changes it),
and a route transition is shown in the preview and reported as `route_profile_before` →
`route_profile`. Before running a session that will
report a finding about Yoetz's semantic behaviour, walk the
[semantic dogfood runbook](semantic-dogfood.md) — it declares up front which claim the run is
allowed to make, and refuses to score semantic quality when no provider attempt happened. To measure
whether feedback **changed the work product** (not merely whether Yoetz was healthy or authorable),
use the [influence dogfood runbook](influence-dogfood.md).

If the host is configured with Yoetz as an optional server and it is unavailable, Codex work
continues and the skill discloses no live ledger/check/receipt data. If configured as required,
server failure blocks only the Codex surfaces that the tested capability profile proves are
affected. Installing the skill never itself changes MCP configuration or produces a receipt.

The exact capability profile also reports whether a compaction-recovery trigger hook and/or a
first-party observation arm is present. A present v0.1 trigger only prompts the agent to re-ground
by calling `status` — it records no observation, changes no coverage, and remains optional. When
the cell advertises observation, enablement requires one project-level observation consent
(workspace commitment, never a raw path in logs); live ingest uses local control methods
(`observation_ingest|status|pause|resume|revoke`), not a seventh MCP tool. `hook_observed` is earned
only from real observation evidence. `AdviceSnapshot` surfaces via nonblocking hooks and ordinary
`status`. Skill installation never configures hooks. If the profile is absent or a trigger/observation
path fails, use the ordinary manual resume/compaction procedure and cooperative publication; do not
infer support from a different Codex version.

For supported content-bearing Codex events, the ready service secret-scans and encrypts selected
tool output, changed-file, and workspace-diff bytes before materializing their exact digest/object
bindings as `observation_captured` ledger evidence. Inspection facts and bounded excerpts receive
separate evidence records. This proves retained byte identity only; it is not an approved check,
artifact verification, independent reproduction, or permission to send the bytes to a model.

### Legacy synchronous-hook latency

Codex versions older than `0.148.0-alpha.6` use a synchronous `hooks spool` command for
`PreToolUse`, `PermissionRequest`, and the ingress half of `PostToolUse`. It performs one fsync'd,
structural-only append and must not connect to the service, drain an outbox, or hydrate the local
observation store. The READY service forwards those records asynchronously through the normal
fenced outbox path. The proposed (issue #362) host-visible budget is p95 `<=250ms`, with a hard
`500ms` cap per synchronous leg including process startup. `yoetz observe status` reports pending
spool work as a coverage gap (`source_lag`), and its hook diagnostics retain the host-visible total
and `sync_fallback_spool` path. Do not treat a pending spool as delivered evidence; keep the
service running and wait for it to drain before making receipt claims.

### Capture and compare the live tool boundary

When a supported Codex build renders a Yoetz argument as `unknown`, capture the client inventory
before reading implementation source or changing a schema. Use a new scratch testing home; the
`codex-testing` launcher derives its real `CODEX_HOME` from `CODEX_TESTING_HOME`, so setting only
`CODEX_HOME` is not isolation.

```text
YOETZ_CODEX_SCRATCH="$(mktemp -d /private/tmp/yoetz-codex-boundary.XXXXXX)"

CODEX_TESTING_HOME="$YOETZ_CODEX_SCRATCH" codex-testing mcp add \
  --env UV_CACHE_DIR="$YOETZ_CODEX_SCRATCH/uv-cache" \
  yoetz -- uv --directory /absolute/path/to/yoetz-core run yoetz mcp serve --semantic off

CODEX_TESTING_HOME="$YOETZ_CODEX_SCRATCH" codex-testing mcp get yoetz

python scripts/capture_codex_mcp_surface.py \
  --codex-binary /absolute/path/to/codex-testing \
  --codex-testing-home "$YOETZ_CODEX_SCRATCH" \
  --output "$YOETZ_CODEX_SCRATCH/mcp-server-status.json"
```

Confirm `mcp get` names only the scratch registration before capturing. Record the exact Codex
build, Yoetz commit or artifact digest, route profile, capture digest, and whether the evidence is
raw `mcpServerStatus` inventory, a declaration actually delivered to a model, or both. Compare
`start`, `publish_work`, and `check` for local references, union-only array items, and conditionals
in object-shape position. A raw inventory proves what Codex received from Yoetz; it does not by
itself prove what a model was shown. Keep before and after captures side by side and state that
evidence boundary explicitly.

Claim correction is an ordinary `publish_work` capability, not a Codex-hook mapping. A current
descriptor advertises `publish-work-request/1.1.0`, which admits `claim_recorded/1.1.0`; older
descriptors remain limited to the frozen v1.0 draft union. The CLI command uses the same public
request and service boundary. Neither Codex hooks nor imported observations synthesize, replace,
or supersede claims.

## 8. Remove

Skill removal and activation/MCP removal are separate, consent-gated operations. Skill removal
never deletes marketplace, `config.toml`, cache, or MCP entries. Activation removal never deletes
the skill tree.

### Skill tree

```text
yoetz integrate codex skill preview --json
yoetz integrate codex skill remove --json
```

Confirm the exact preview digest. Removal deletes only a valid managed marker plus its byte-exact
file inventory. Modified, partial, or unmanaged content is refused and preserved — there is no
force-remove in v0.1. Removal never uninstalls the Yoetz package, deletes MCP configuration, deletes
ledger/key data, or touches other skills. Verify `status` shows `absent` afterward.

### Plugin, marketplace, and `config.toml`

```text
yoetz integrate codex plugin preview --codex-home <home> --json
yoetz integrate codex plugin remove --codex-home <home> --accept --preview-digest <digest> --json
```

`preview`, `status`, and `remove` are the whole Codex plugin command surface. The generic
`install`, `update`, `enable`, `disable`, and `export` commands that the shared
`integrate <host> plugin` group also lists belong to the Claude Code (and, for `install`, Cursor)
lifecycles; `--help` marks each command's hosts, and invoking one for Codex refuses with
`codex_plugin_command_unsupported:<command> supported=preview,status,remove` (exit 2) before any
binary discovery or mutation. Codex activation is the digest-bound setup/recommendation ceremony
(`yoetz setup run`, ADR-012), not a standalone plugin command.

The Codex plugin command uses the same preview → explicit accept → apply shape as Codex
activation and MCP install: the mutation is bound to the exact preview digest. It does **not**
consume the Cursor `plugin_artifact_apply` OS-presence cell; that cell remains the standalone
portable-artifact authority and would fail closed on this host. Cache purge is default-off.
`--purge-cache` additionally deletes other version directories under
`<codex-home>/plugins/cache/yoetz/yoetz/<ver>` whose trees byte-match a yoetz render or their own
valid `yoetz.codex-plugin-install/1` marker. Foreign or modified cache directories are refused
(`remove_refused`, conflict `cache`) and left untouched. Preview and apply use the same no-follow,
descriptor-relative 256-total-entry, 16-level, 4-KiB-relative-path, 64-file,
256-KiB-per-member, and 4-MiB-aggregate bounds, so directory-only, deep, sparse, and oversized
trees fail closed before unbounded allocation or recursion. Apply retains the validated version
descriptor through quarantine rename and rechecks the exact approved names and bytes immediately
before and during unlink; newly observed names are never swept into deletion. Observable drift
before the first unlink restores the retained inode to its exact version name; later drift
preserves the remaining quarantine. Both report `write_failed` because quarantine rename already
crossed the mutation boundary. Keep same-UID cache writers
quiescent during removal because ordinary POSIX files provide no atomic compare-and-unlink token
for the last content-write window.

Apply runs `codex plugin remove yoetz@yoetz --json`, then `codex plugin marketplace remove yoetz
--json` when `[marketplaces.yoetz]` byte-matches the yoetz render, then deletes
`<repo>/.agents/plugins/marketplace.json` only when a retained no-follow descriptor still byte- and
inode-matches through a private quarantine rename, then
deletes the bound current-version cache. Whole-table TOML edits are verified by re-parse. A second
removal is a no-op (`already_absent`). Foreign, modified, dual, or otherwise conflicting entries
refuse with `remove_refused` and name the conflicting surface (`personal_marketplace`,
`repository_marketplace`, `config_marketplace`, `config_plugin`, `inventory`, or `cache`). Before
mutation, changed preview-bound bytes report `preview_stale`.
After a mutating host command starts (including a zero-exit command with malformed JSON), config
write, marketplace quarantine/unlink, cache quarantine rename, or member unlink has
started, any newly observed conflict reports `write_failed` (with the bounded conflict token when
available) because the outcome may be partial; it is never mislabeled as a safe stale-preview
retry.

After a successful removal, `codex plugin list --marketplace yoetz --json` is empty and
`config.toml` has no yoetz tables. `yoetz observe status` reports the existing activation
classification: `installed_not_activated` when the managed plugin source at
`.agents/plugins/yoetz` remains (issues #387 and #347), including a modified copy, or `not_installed` when that source is also absent.
The command reports whether the skill tree remains; it does not remove it. Consent records and the
observation store are intentionally left in place.

### External MCP registration

```text
yoetz integrate codex mcp preview-remove --json
yoetz integrate codex mcp remove --accept --preview-digest <digest> --json
```

The first command exposes the exact unregistration digest and current owned route without
mutation. Noninteractive removal requires that digest plus `--accept`; `--accept` alone fails
closed. Apply re-reads the current entry immediately before it runs `codex mcp remove yoetz` and
refuses a foreign replacement or changed Yoetz route observed at that boundary. An
already-absent entry is a no-op only after the same successful `mcp list --json` absence check.
Interactive removal shows the exact command, route profile, warning tokens, and preview digest
before requesting confirmation.

Codex 0.149.x exposes a name-based remove command, not a compare-and-remove token. The owned-entry
preview therefore includes `host_remove_not_compare_and_swap`: the owner must keep concurrent
Codex MCP configuration writers quiescent during the accepted apply. The immediate pre-remove
recheck narrows the host limitation, but cannot atomically exclude a non-cooperating replacement
inside the final subprocess scheduling window. Post-apply verification still fails closed if the
entry is not positively observed absent; a generic failed named lookup is not success.
Plugin-managed MCP is not this command: it goes away with the plugin artifact, not with `codex mcp
remove`.

## 9. Bounded `codex exec --json` import

The import support command is Codex-only and local. It accepts the exact request documented in
[`docs/usage/importing-codex-jsonl.md`](../usage/importing-codex-jsonl.md); it does not read rollout
files, add an MCP operation, hook event, or dedicated TUI screen, and it does not change
external-review policy. The CLI import and consent commands are the owning terminal surfaces.

The first `yoetz import --input <request> --json` call must stop with
`PRIVACY_AUTHORITY_REQUIRED` after the source and plan are durable. Run
`yoetz consent status --json` and review the `import_publication_preview`. The preview is
structural only: never copy source lines or excerpts into an agent chat. For agent-attested
authorization, show the exact danger text and digests, wait for an explicit current-chat approve
or deny instruction, then relay that exact pending item through `yoetz consent authorize` with
`--warning-acknowledged`. Agent attestation is not independent proof. After approval, replay the
identical import request; do not add an approval argument or mint a new request ID.

The owner-only authorization survives a service restart only for the same stored plan. It is
consumed after terminal completion. A source, manifest, target task/session/writer,
profile/version, mapping, plan, or limit change must produce another preview. Denial, expiry, or a
different pending consent publishes nothing. Import intake never authorizes semantic-provider or
reviewer egress.

## 10. Troubleshooting and recovery

| Symptom | Action |
|---|---|
| Target untrusted/unsafe | Correct the explicit root/permissions; there is no force option. |
| Resource invalid | Reinstall from a verified package artifact. |
| Preview stale | Run `status`, then a fresh preview. |
| Modified/partial content | Preserve and review manually; use `replace_modified` deliberately if desired. |
| Compatibility is `unsupported` | Automatic activation is unprofiled; use a supported Yoetz/Codex version pair when capability evidence is required. |
| Write/swap interrupted | Run `status`; preserve any staged content; do not delete it yourself. |
| Skill not discovered, or duplicate `$yoetz` names loaded | Check the exact scope, loaded skill roots, managed path, trust, version, and capability matrix; reload Codex. |
| Setup reports `installed_not_activated` | Run `yoetz recommend list --codex-path <exact-executable> --codex-home <exact-home>`, then accept only the freshly shown target/preview digest. Historical acceptance cannot suppress an observed inactive target; a decline suppresses only its unchanged exact target. Review canonical inventory and the versioned cache; marketplace/config presence alone is insufficient. A marker-consistent stale cache is refreshed by an ordinary approved re-run. |
| Activation reports `destination_conflict` | The versioned cache (or a config/marketplace surface) holds foreign, marker-inconsistent, or modified content. Review it by hand; setup only replaces trees that match their own Yoetz install marker. |
| Activation failed with an explicit `--codex-home` | Read the actual `reason` in `registration.plugin_activation`/`readiness.plugin_activation`; the bound home and config path are echoed there. `codex_home_required` appears only when no home was passed. |
| Setup reports plugin source files but no Yoetz skill appears | Check `.agents/skills/yoetz`; source installation and plugin activation do not prove project-skill discovery. |
| MCP name already present | Preserve it and review ownership rather than running `mcp add`. |
| `setup` skipped MCP registration | Codex not on PATH, or the entry is foreign-owned; run `yoetz integrate codex mcp status --json` for the exact state. |
| MCP unavailable | Diagnose through separate MCP configuration/startup steps. |
| Trigger absent or failed | Use the manual re-grounding procedure; never edit hook configuration through this integration. |
| `observe status` shows no envelopes for a session | Read `hook_diagnostics.reasons`: `workspace_unresolvable` means the hook's `--workspace` locator could not be canonicalized; `workspace_unconsented` means the session's Git root carries no active consent (a session started in a subdirectory canonicalizes to the same root as the consent, so grant consent at the repository root); `paused` means consent is paused. A successful ingest records no diagnostic, so read `recent_count` together with the envelopes: no new envelopes and a zero `recent_count` means the hooks never reached the ingress or the runtime gate is disabled, not that a binding drop occurred. |
| `observe status` shows `mapping_present: false` after a consented `SessionStart` | The hook sends `start mode=create_or_attach` with the canonical `--workspace` root as `workspace_ref` and `codex-session:<session_id>` as `external_ref`; read `hook_diagnostics.reasons` for the typed cause: `auto_attach_workspace_unbound` (the session bound consent without a canonical locator, so no paired request was legal), `auto_attach_request_invalid` (an authoring defect in the request — file it), `auto_attach_conflict` / `auto_attach_refused` (the service answered and declined), `auto_attach_result_invalid`, `auto_attach_mapping_write_failed`, `privacy_authority_required`, `vault_locked`, `timeout`, `storage_unsafe` / `storage_corrupt`, or `service_unavailable` (the daemon was still starting; `UserPromptSubmit`, `Stop`, and `SessionEnd` retry under the bounded budget and add `auto_attach_retry_failed` beside the cause). An explicit MCP `start` remains the recovery path; for `vault_locked` on a never-initialized install, that `start` returns the typed `vault_initialization_required` continuation below rather than a dead end. |
| `observe status` shows `ledger_rejected` and `outbox_quarantined` | The service was reachable but rejected one envelope non-retryably. The row is retained under `quarantine_causes`, aggregate `delivery_causes`, and gaps; `pending_delivery_causes` names only rows still in the outbox. Later rows can drain; reclaim only after the underlying defect is understood. A hook-driven attempt also appears in the bounded `hook_diagnostics`, while manual and supervisor drains are represented by status rather than hook activity. Do not restart a ready service. A row is also quarantined after 128 consecutive rejections with the same retryable reason so a catch-all failure cannot block the lane forever; pause, vault, disabled, and designed back-pressure reasons keep their existing recovery behavior. |
| `observe status` exits with `observation_status_failed:<reason>` | The reason names the layer: `workspace_unresolvable` (exit 2) is the locator; `storage_unsafe` (exit 20) is an unsafe state/lock path; `storage_unavailable` (exit 20) is a bounded open, permission, read-only, missing-parent, or lock-acquisition failure; `storage_corrupt` (exit 40) is invalid stored data. The fixed remediation never prints the absolute state path. A sandboxed Codex result proves only that sandbox cell; run and record an unrestricted-terminal comparison separately before making that claim. |

## 11. Security, privacy, and prohibited actions

Codex is the v0.1 allowlisted first-party client for exact current-chat consent attestation.
It should guide Yoetz setup, installation, and settings changes in normal conversation: explain
each consequential choice, recommend one outcome with its trade-off, and preserve the user's
explicit selection. When the user explicitly wants semantic review, recommend Expanded first for
review depth and explain Assisted as the lower-disclosure semantic option. Do not silently
downgrade either choice.

For `repository_privacy_grant`, run the catalog-advertised prepare command only after the recipe is
chosen. Show the v6 `repository_privacy_preview` in full: repository commitment; authority,
current-policy, candidate-policy, and diff digests; and every before/after row including the exact
provider/model/endpoint. The user's final approve/deny applies only to that expiring one-use target.
Any repository, authority, configured-route, recipe, target, expiry, or replay drift is a
no-mutation failure; never prepare a replacement silently. Strict MCP routing, host admission,
provider readiness, physical dispatch, and receipts remain separate facts.

For `vault_initialize` and `vault_passphrase_rotate`, it relays only the prepared pending ID,
operation, danger digest, target digest, decision, and warning acknowledgement. Yoetz generates,
loads, stages, and submits vault secrets inside the local helper; Codex must never request or
receive them. An ambiguous rotation preserves its staged entry for service-restart reconciliation.

First-run start continuation (issue #512): on a never-initialized install, the agent's first MCP
`start` returns non-retryable `VAULT_LOCKED` carrying
`safe_details.continuation: vault_initialization_required` with exact-literal `prepare_command`,
`review_command`, `authorize_command`, the pending TTL, and `replay_request_id`. The Codex flow
is: run `yoetz consent prepare vault_initialize`, show the returned danger text and digests in
chat, wait for the user's explicit current-chat approve or deny, relay exactly that decision via
`yoetz consent authorize` with `--warning-acknowledged` (agent attestation is not independent
proof), then replay the exact original `start` request ID and body once. Denial, expiry, and
every ceremony failure remain their distinct bounded outcomes; a hard-locked initialized vault
never carries this continuation and keeps the unlock/recovery paths.

Never paste modified skill content, repository content, paths, Codex configuration, a transcript, a
prompt, a key, an environment variable, or a raw exception into public support. Share only versions,
state, source/installed/preview digests, the bounded reason token, and file-state names.

- Never claim a global or fuzzy install scope.
- Never force an overwrite or a removal.
- Never claim skill installation changed MCP configuration.
- Never claim support for a Codex version outside the current tested set.

For a disposable-worktree integration run, use the [Codex dogfood parity
runbook](codex-dogfood.md). The ordinary setup/semantic checks above are necessary but do not prove
exact-worktree activation, consent, host delivery, observation, rollback, or normal-target
isolation. In particular, an isolated Codex home (`CODEX_TESTING_HOME`) does not isolate Yoetz:
without `YOETZ_ISOLATED_ROOT` (ADR-026) exported to every tested process, the run's Yoetz clients
and any service they spawn resolve the normal singleton, state directory, and storage. Prove the
mode with `yoetz service isolation --json` before launch; the parity gate's `service_isolation`
facet fails closed on shared, ambient, or unknown identity.

## Subscription evaluator is a separate Codex role

Codex may be both the host carrying Yoetz and the selected external semantic evaluator, but those
are independent cells. Host skill/plugin/MCP activation grants no ChatGPT evaluator login or
privacy authority. Configure the evaluator only through
`yoetz provider codex-subscription setup`; it binds a separate owner-private `CODEX_HOME` and exact
native `0.150.1` app-server cell. Never reuse the host's ambient home, environment, session, tools,
instructions, or repository cwd for the evaluator.

The registered host route still decides whether this Codex process may request semantic work:
`strict` proves zero evaluator launch, while `policy` only permits ADR-009 to decide. Read the
[subscription evaluator runbook](codex-subscription-evaluator.md) before claiming live model use,
runtime isolation, privacy receipt, or cleanup.

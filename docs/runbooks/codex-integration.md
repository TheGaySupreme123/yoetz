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

Run `codex mcp get yoetz --json` first. Continue with `mcp add` only when no entry exists — if an
entry already exists, preserve it and stop unless a separately reviewed operation proves it is the
exact Yoetz-owned registration being intentionally replaced. Current Codex `mcp add` behavior
replaces a same-name global entry, so this preflight check matters.

This check-then-add sequence is also available as
`yoetz integrate codex mcp status|preview|install|remove` and is what `yoetz setup run` performs after
Codex discovery (ADR-012). Automating it changes no rule above — it is the same two commands,
gated by an explicit digest-bound confirmation, run by Yoetz instead of by hand; an existing
foreign entry is still preserved and refused, success is verified by re-reading the entry, and
"registered" still never implies Codex will successfully connect at runtime.

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

The preview digest also binds the repository marketplace and selected-home config
preimages/proposals, managed source-tree digest, cache root
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
states are `installed_not_activated`, `not_installed`, and `foreign`. None of them—and not even
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

The Codex plugin command uses the same preview → explicit accept → apply shape as Codex
activation and MCP install: the mutation is bound to the exact preview digest. It does **not**
consume the Cursor `plugin_artifact_apply` OS-presence cell; that cell remains the standalone
portable-artifact authority and would fail closed on this host. Cache purge is default-off.
`--purge-cache` additionally deletes other version directories under
`<codex-home>/plugins/cache/yoetz/yoetz/<ver>` whose trees byte-match a yoetz render or their own
valid `yoetz.codex-plugin-install/1` marker. Foreign or modified cache directories are refused
(`remove_refused`, conflict `cache`) and left untouched.

Apply runs `codex plugin remove yoetz@yoetz --json`, then `codex plugin marketplace remove yoetz
--json` when `[marketplaces.yoetz]` byte-matches the yoetz render, then deletes
`<repo>/.agents/plugins/marketplace.json` only when that file byte-matches the yoetz render, then
deletes the bound current-version cache. Whole-table TOML edits are verified by re-parse. A second
removal is a no-op (`already_absent`). Foreign, modified, dual, or otherwise conflicting entries
refuse with `remove_refused` and name the conflicting surface (`repository_marketplace`,
`personal_marketplace`, `config_marketplace`, `config_plugin`, `inventory`, or `cache`).

After a successful removal, `codex plugin list --marketplace yoetz --json` is empty and
`config.toml` has no yoetz tables. `yoetz observe status` reports the existing activation
classification: `installed_not_activated` when the managed plugin source at
`.agents/plugins/yoetz` remains (issue #387), or `not_installed` when that source is also absent.
The command reports whether the skill tree remains; it does not remove it. Consent records and the
observation store are intentionally left in place.

### External MCP registration

```text
yoetz integrate codex mcp remove --accept --preview-digest <digest> --json
```

This removes an `external_registration` Codex MCP entry by running `codex mcp remove yoetz` only
when the registered argv is an exact Yoetz serve command. A foreign same-name entry is preserved
and refused. An already-absent entry is a no-op. Plugin-managed MCP is not this command: it goes
away with the plugin artifact, not with `codex mcp remove`.

## 9. Troubleshooting and recovery

| Symptom | Action |
|---|---|
| Target untrusted/unsafe | Correct the explicit root/permissions; there is no force option. |
| Resource invalid | Reinstall from a verified package artifact. |
| Preview stale | Run `status`, then a fresh preview. |
| Modified/partial content | Preserve and review manually; use `replace_modified` deliberately if desired. |
| Compatibility is `unsupported` | Automatic activation is unprofiled; use a supported Yoetz/Codex version pair when capability evidence is required. |
| Write/swap interrupted | Run `status`; preserve any staged content; do not delete it yourself. |
| Skill not discovered, or duplicate `$yoetz` names loaded | Check the exact scope, loaded skill roots, managed path, trust, version, and capability matrix; reload Codex. |
| Setup reports `installed_not_activated` | Re-run setup/recommendation preview for the exact selected executable. Review canonical inventory and the versioned cache; marketplace/config presence alone is insufficient. A marker-consistent stale cache is refreshed by an ordinary approved re-run. |
| Activation reports `destination_conflict` | The versioned cache (or a config/marketplace surface) holds foreign, marker-inconsistent, or modified content. Review it by hand; setup only replaces trees that match their own Yoetz install marker. |
| Activation failed with an explicit `--codex-home` | Read the actual `reason` in `registration.plugin_activation`/`readiness.plugin_activation`; the bound home and config path are echoed there. `codex_home_required` appears only when no home was passed. |
| Setup reports plugin source files but no Yoetz skill appears | Check `.agents/skills/yoetz`; source installation and plugin activation do not prove project-skill discovery. |
| MCP name already present | Preserve it and review ownership rather than running `mcp add`. |
| `setup` skipped MCP registration | Codex not on PATH, or the entry is foreign-owned; run `yoetz integrate codex mcp status --json` for the exact state. |
| MCP unavailable | Diagnose through separate MCP configuration/startup steps. |
| Trigger absent or failed | Use the manual re-grounding procedure; never edit hook configuration through this integration. |

## 10. Security, privacy, and prohibited actions

Never paste modified skill content, repository content, paths, Codex configuration, a transcript, a
prompt, a key, an environment variable, or a raw exception into public support. Share only versions,
state, source/installed/preview digests, the bounded reason token, and file-state names.

- Never claim a global or fuzzy install scope.
- Never force an overwrite or a removal.
- Never claim skill installation changed MCP configuration.
- Never claim support for a Codex version outside the current tested set.

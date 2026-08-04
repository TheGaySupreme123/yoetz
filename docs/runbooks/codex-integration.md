# Codex integration runbook

This runbook guides you through previewing, installing, checking, replacing, and removing the
canonical Yoetz Codex skill in one explicitly trusted project, while preserving any files you have
modified. It also separates three facts that are easy to conflate: skill installation, MCP
registration, and Codex's tested capability for a given Codex version.

## 1. What integration installs — and does not

v0.1 installs only the canonical `SKILL.md`, its named references/compatibility data, and a
nonsecret managed marker, at exactly:

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

## 2. Prerequisites and exact supported scope

Check `yoetz version --json`, the installed resource set, and the current compatibility/capability
matrix. Confirm owner permissions on the target project, that you trust this repository, and the
expected Codex version. Codex support is the exact tested set in the packaged manifest; an empty
set means no Codex release currently carries automatic-activation support evidence. A version
string or successful file install never promotes an unprofiled release to supported.

## 3. Status and preview

Always run status first:

```text
yoetz integrate skill status codex --json
```

Destination states: `absent`, `installed_exact`, `modified`/`unmanaged`, `partial`, `unsafe`.
Compatibility is reported separately as `supported`, `unsupported`, or `untested`.
Status is read-only — it never repairs or updates anything. An identical directory without a valid
managed marker is treated as unmanaged/modified and is protected from removal. `installed_exact`
does **not** by itself prove Codex has discovered the skill or that MCP is available.

## 4. First install

```text
yoetz integrate skill preview codex --json
yoetz integrate skill install codex --json
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
`yoetz integrate codex mcp status|preview|install` and is what `yoetz setup run` performs after
Codex discovery (ADR-012). Automating it changes no rule above — it is the same two commands,
gated by an explicit digest-bound confirmation, run by Yoetz instead of by hand; an existing
foreign entry is still preserved and refused, success is verified by re-reading the entry, and
"registered" still never implies Codex will successfully connect at runtime.

The accepted setup path composes three separately reported layers in order: it installs the
project skill at `.agents/skills/yoetz`, installs managed structural plugin/hook sources at
`.agents/plugins/yoetz`, then verifies the MCP entry. The plugin source directory is not evidence
that Codex activated a plugin. Current Codex plugin activation has its own marketplace and explicit
add trust flow; setup deliberately does not mutate that global surface. This distinction is why a
successful setup report includes both project-skill presence and plugin-source presence.

Registration also decides *which* route the agent gets. Both owned serve commands classify as
`yoetz_owned`, so the state alone cannot tell a strict registration from a policy one. Read the
route from `yoetz integrate codex mcp status --json` (`route_profile`) or from
`yoetz provider status --json` (`mcp_route.registered_profile`). Before running a session that will
report a finding about Yoetz's semantic behaviour, walk the
[semantic dogfood runbook](semantic-dogfood.md) — it declares up front which claim the run is
allowed to make, and refuses to score semantic quality when no provider attempt happened.

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

```text
yoetz integrate skill preview codex --json
yoetz integrate skill remove codex --json
```

Confirm the exact preview digest. Removal deletes only a valid managed marker plus its byte-exact
file inventory. Modified, partial, or unmanaged content is refused and preserved — there is no
force-remove in v0.1. Removal never uninstalls the Yoetz package, deletes MCP configuration, deletes
ledger/key data, or touches other skills. Verify `status` shows `absent` afterward, and separately
manage any MCP configuration yourself if you want it removed too.

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
| Setup reports plugin source files but no Yoetz skill appears | Check `.agents/skills/yoetz`; `.agents/plugins/yoetz` alone is not an activated Codex plugin. |
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

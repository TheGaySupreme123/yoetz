# Codex integration runbook

For setup prerequisites, use `yoetz setup status --next --host codex` with the same
executable, configuration root and project. `--operation connection` inspects installation without
provider sign-in; `local` and `review` inspect their respective vault/privacy prerequisites.
Storage-only continuation is `yoetz setup vault` in a trusted terminal. This shared #737 path adds
no new native-session capability; Linux/WSL first-use and storage acceptance remain bounded by
[the platform runbook](linux-and-wsl.md#setup-and-vault-acceptance-still-owned-by-737).

## Guided desktop connection (issue #767)

`yoetz setup run --host codex` exposes the existing project-skill, plugin, activation and MCP
composition through the common desktop connection plan. Discovery offers the installation;
`--host-path`, `--host-config-root`, and `--project` select exact alternatives. An agent requests
`--non-interactive --json`, presents the changes, then repeats the returned request ID and preview
digest with `--accept`. The selected home remains bound through every Codex probe and mutation.
The historical `--codex-path`/`--codex-home` wizard remains a compatibility path.

`setup status|disconnect --host codex` uses the same target options. Disconnect removes activation,
the owned MCP entry and exact project skill, retaining inactive plugin sources and Yoetz data.
Modified or foreign integration state is refused. Record native installation, fresh-session
discovery/start, disconnect and reconnect for macOS, Linux and WSL 2 separately in #767. Desktop
app availability and CLI availability remain distinct; an untested version/platform does not
inherit certification from executable discovery or unit tests.

## Linux and WSL

This integration uses the standalone Codex CLI installation path on Linux and WSL 2. This runbook
does not configure or certify a Linux desktop application. The TUI labels a Linux executable as
`Codex CLI` and reserves `Codex Desktop` for an application bundle path, describing the installation
shape rather than activation or session capability. The common setup flow, service, and project binding still require the same
explicit target and fresh-session evidence as macOS. Native Linux/WSL capability remains bounded
by its recorded evidence; installation discovery and TUI labels do not imply a native-session
capability. Shared facts are in the [Linux/WSL runbook](linux-and-wsl.md), and the installation
evidence is recorded in #767.

## Conditional agent guidance

The skill keeps its activation boundary, core workflow, and safety floor in the entrypoint.
Read workflow guidance before `start`, publication policy before `publish_work`, and coverage
and receipts before `check`. Setup/consent, vault/credential operations, transcript import, and
recommendation decisions route to the corresponding sections of request templates only when
needed. Already-read guidance need not be fetched again while present in context.

Use `semantic_if_configured` only when AI-powered review is known to be optional; omit `mode` when
relying on the configured default. Select `semantic_required` for an explicit user requirement,
effective policy, or named acceptance criterion requiring independent AI-powered judgment. Preserve
required review and all host/disclosure approval boundaries. Installed guidance bytes alone prove
neither activation nor AI-powered review dispatch.

The Codex skill routes to the existing five MCP guidance URIs with installed reference fallbacks.
The server initializes only the safety floor. Consumer source-inspection restrictions do not
prohibit developing or debugging Yoetz itself against isolated test state.

A new Codex session reads guidance and discovers tool schemas before calling MCP `start` as
its first workflow operation, before substantive research, commands, edits, or delegation.
Guidance reads (including `read_guidance`), discovery commands, and necessary bootstrap clarification
remain permitted. Hook auto-attachment is a cue, not proof of a current-scope plan: mapped
SessionStart context directs cooperative `start mode=attach` before `status` with the returned
ids; unmapped context directs `start` before material work. Same-session compaction uses held
current ids for `status`. Both startup messages route failures through exact continuations,
same-request recovery, and a named one-time repair before a blocked-startup user handoff.
A first non-retryable failure alone does not permit continuing without Yoetz; see
[startup failure precedence](../../guidance/coverage-and-receipts.md#startup-failure-precedence).
Codex has no demonstrated PreToolUse deny gate; instruction delivery is not enforcement (#692).
`--startup-mode required` is refused for Codex. The [native Claude/Cursor gate](required-startup.md)
does not expand Codex capability claims.

The Codex entrypoint is written directly for Codex and selected by its installer. The other native
installers select their own skills. Keep installed-source evidence separate from compatibility
directory discovery; a project skill discovered elsewhere is not proof of this integration.

Design basis, checked 2026-09-09: OpenAI's
[Skill Creator](https://github.com/openai/codex/blob/main/codex-rs/skills/src/assets/samples/skill-creator/SKILL.md)
assumes Codex is capable and recommends task-specific constraints, precise discovery metadata,
and conditional references. Its
[PR comment skill](https://github.com/openai/skills/blob/main/skills/.curated/gh-address-comments/SKILL.md)
uses a short operational sequence and a helper for repetitive mechanics. Yoetz keeps its exact
MCP read fallback and bounded closure composer because those address demonstrated authoring
failures; generic host-identification instructions are unnecessary. The five protocol references
retain one canonical owner and are copied into each installed skill.

Cursor's explicit-start repair (issue #661) leaves Codex's lifecycle binder and hook subscription
unchanged. [The official hooks reference](https://developers.openai.com/codex/hooks), checked
2026-09-08, specifies `tool_response` containing the MCP call result on `PostToolUse`, with
canonical `mcp__server__tool` names. Codex keeps its existing result unwrapping and lifecycle
locking; Cursor-only response fields and server identities are normalized only in Cursor's
adapter. The Codex lifecycle and observation regression tests remain the compatibility check.

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
policy owner can make `agent_route_semantic_ready` true. If more than one Codex executable is
discovered, provide `--codex-path <exact-executable>` so the report can inspect one selected
installation; the report keeps the route unread and supplies a bounded continuation retaining the
selected home, launcher and isolation root when no selector is given.

## 2. Prerequisites and exact supported scope

### Explicit attach under observation contention

An explicit `start mode=attach` can meet an existing observation/advisory runtime lease.
The service waits at most five seconds for same-bundle use to drain. A retry-ready start
reason retains the reserved operation and releases only its lease; replay the identical
request body and ID once. `start_lease_pending` instead means a live owner still holds the
operation: wait up to 60 seconds before the same replay. A repeated busy/pending answer remains
unresolved. No returned writer ID is needed for this recovery, and no replacement task should
be created. Both structured errors and the compact MCP text carry the bounded continuation.
Hook auto-attachment uses the same service path; a hook deadline may expire before the service
wait finishes, so a lost response remains an exact-request recovery case, never proof of failure.
These runtime tests do not qualify a new native Codex version or prove source-content delivery.

Check `yoetz version --json`, the installed resource set, and the current compatibility/capability
matrix. Confirm owner permissions on the target project, that you trust this repository, and the
expected Codex version. Codex support is the exact tested set in the packaged manifest; an empty
set means no Codex release currently carries automatic-activation support evidence. A version
string or successful file install never promotes an unprofiled release to supported.

Session-stream (rollout) parsing is a separate, narrower fact. `0.148.0` and `0.150.1` each have
an exact fixture-proven rollout grammar profile (`codex-rollout-jsonl/<version>/v1`, ADR-005).
That parser proof is what lets an isolated dogfood run advertise the `session_stream` facet for
that exact release; it is not host support, and it says nothing about skills, MCP, hooks, or
activation. Any other release is admitted under the structural compatibility profile
(`codex-rollout-jsonl/compatible/v1`, ADR-005 structural admission amendment, issue #656): the
version is recorded as provenance, lines whose wrapper/item structure Yoetz already understands
are observed, and unknown or malformed lines stay bounded `unsupported_event` gaps. The reconcile
result reports `admission` as `structurally_supported` (exact profile, every line understood),
`partially_understood` (compatible profile or any unknown line, with `admission_reasons` naming
the affected family), or `incompatible` (a header that is not `session_meta`, a non-object
payload, or an unknown `history_mode` — refused as `unsupported_format`, cursor kept). A
compatible release still earns an exact profile and the `session_stream` facet only through its
own fixtures. Explicit-path recovery uses the same atomic cursor, profile, source identity,
and tool-pairing frontier as automatic reconciliation, including mapping upgrades and delivery
backpressure.

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
codex mcp add yoetz -- yoetz mcp serve --host codex
```

Codex uses the explicit `--host codex` serving identity. This declaration binds route-drift
diagnostics to the Codex carrier; it does not grant host admission or current-chat attestation,
which retain their independent client allowlist. Decision for Codex (issue #579):
supported here — the same bounded text `Reason:` clause as Claude Code names frozen `reason_code`
and `field` on `EVENT_INVALID`, because this host also relies on the privacy-minimized summary
rather than a full JSON text copy.

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

Codex copies the initialize `instructions` into every tool description, so the guardian reads them.
Since issue #479 the policy-route instructions name the AI-powered review destination read at bridge
startup (provider, endpoint profile, and host, or the Codex runtime class) and the payload bound.
That gives the reviewer a named destination to score instead of nothing; it is still not trusted
user content, admits nothing, and goes stale until Codex restarts the bridge. Admission remains the
lever.

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

Complete Codex's own trust step in a fresh native process before testing hook delivery. Open
`/hooks`, review the commands from the intended Yoetz plugin, and approve those hooks through
Codex's normal review UI. Confirm the hooks are active, then start a fresh session so its
`SessionStart` runs with that trust. New or changed hooks can remain installed but inactive;
the plugin's skill and MCP tools may still work in that state. A non-interactive run with no
observation rows is therefore not, by itself, evidence of a Yoetz ingestion failure. Installation
inspection reports hook trust as unknown because it does not inspect Codex's effective trust
decision. Do not replace this check with a hook-trust bypass flag.

Project trust is a separate gate for the project-local MCP admission table above. When a check
returns `MCP tool call requires approval, but approval policy is never`, it was refused by Codex
before Yoetz dispatched it. Confirm the project is trusted in the selected Codex home and test a
fresh process with the intended MCP owner and per-tool policy. An installed admission entry or
`codex mcp get` output alone does not prove the running process applied that policy. A normal
interactive process can present any remaining approval request without changing the sandbox.

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
route from `yoetz integrate codex mcp status --json` (`route_profile`) or from `yoetz provider
status --json` (`mcp_route.registered_profile`). The route is explicit input: pass `--route-profile
strict|policy` to `yoetz setup run` or `yoetz integrate codex mcp preview|install` to choose it.
Without that flag an existing yoetz-owned registration keeps its current route (non-interactive
`--accept` never changes it), and a route transition is shown in the preview and reported as
`route_profile_before` → `route_profile`. Before running a session that will report a finding about
Yoetz's AI-powered review behaviour, walk the [AI-powered review dogfood
runbook](semantic-dogfood.md) — it declares up front which claim the run is allowed to make, and
refuses to score AI-powered review quality when no provider attempt happened. To measure whether
feedback **changed the work product** (not merely whether Yoetz was healthy or authorable), use the
[influence dogfood runbook](influence-dogfood.md).

### Applied-route record and registration drift (issue #537)

Decision for Codex: this host gets the state-root applied-route record. Every accepted
`mcp install` writes the applied route profile, the exact serve command, the post-write
observation, and structural digests to the owner-only state-root record
(`integrations/applied-mcp-routes.json`); later installs overwrite it, and `mcp remove`
clears it — removal ends the install it recorded, so absence with no record reads as no
drift and a later install writes a fresh record. `yoetz integrate codex mcp status --json` and `yoetz provider status --json`
(`mcp_route.applied_profile`, `mcp_route.drift_since_install`) join that record against the
live host resolution, fail-soft: an unreadable record reads as no applied route and never
reports drift. `yoetz setup status --json` stays discovery-only by design: it reports
discovered binaries and live registration state without the applied-route drift join, which
lives in `mcp status` and `provider status`. An accepted install that had nothing to write
(`action: noop`) still refreshes the record, so a deliberate re-registration of the route the
host already serves never leaves an earlier entry behind to report as drift.

The MCP bridge (`yoetz mcp serve --host codex`) is the sole emitter of the closed `registration_drift`
hook diagnostic: at startup it compares its own serving argv against the applied record and
records the mismatch under the `mcp_serve` event. The hook paths deliberately emit nothing.
A hook process has no serving route of its own, so the only comparison available there is a
`codex mcp get` subprocess plus the PATH version probes needed to find the binary, which
costs a large fraction of the end-to-end hook budget and is bounded only by the adapter's own
10s command timeout — the failure mode of #209-#213. The bridge starts for the same Codex
session and answers the same question for free, against stronger evidence (what this process
actually serves, not what the host would resolve for the next one).

A strict-ceiling check served while the applied record says `policy` carries the
`optional_semantic_review_registration_drift` coverage gap so its receipt names the recovery
(re-register the policy route, start a fresh Codex process). A genuinely applied strict route
keeps the terminal ceiling wording. The drift gap is never carried onto a later
local-only check: it is re-added fresh on the strict-ceiling path only, after reading
the live record.

If the host is configured with Yoetz as an optional server and it is unavailable, Codex work
continues and the skill discloses no live ledger/check/receipt data. If configured as required,
server failure blocks only the Codex surfaces that the tested capability profile proves are
affected. Installing the skill never itself changes MCP configuration or produces a receipt.

The exact capability profile also reports whether a compaction-recovery trigger hook and/or a
first-party observation arm is present. A present v0.1 trigger only prompts the agent to re-ground
by calling `status` — it records no observation, changes no coverage, and remains optional. When
the cell advertises observation, enablement requires one workspace-level observation consent
(workspace commitment, never a raw path in logs); live ingest uses local control methods
(`observation_ingest|status|pause|resume|revoke`), not a seventh MCP tool. `hook_observed` is earned
only from real observation evidence. `AdviceSnapshot` surfaces via nonblocking hooks and ordinary
`status`. Skill installation never configures hooks. If the profile is absent or a trigger/observation
path fails, use the ordinary manual resume/compaction procedure and cooperative publication; do not
infer support from a different Codex version.

Codex setup's consent step is a structural grant. It uses the same path from `yoetz setup`, host
connection, and the terminal interface. When the workspace already has live consent (for example,
from a Claude Code or Cursor connection), the step keeps that consent: the original grant time,
every approved Claude Code or Cursor content profile, selection and capacity settings, and the
unchanged content fence. Capture already in progress for the other host therefore stays authorized
(#835). The setup report's `observation_consent.transition` reads `unchanged` and lists the retained
`content_capture_profiles`. A paused workspace is resumed (`resumed`). With no prior consent, or
after a completed revoke, consent starts fresh (`granted`) with no content profile. While a revoke's
project fence is pending, the consent step reports `failed` and leaves consent revoked. Only
`observe content-disable` or `observe revoke` removes a host's content arm.

### Subagent correlation (#507)

The Codex native hook artifact includes `SubagentStart` and `SubagentStop`. Their structural
identifiers are normalized as `subagent_id` plus the optional `parent_tool_call_id`; the 0.150.1
rollout grammar also exposes a `SubAgentActivity` record whose bounded `agent_thread_id` maps to
the child identity and whose closed `kind` (`started` or a terminal state) selects the phase. Raw
agent paths, prompts, and summaries are discarded. These mappings are service inputs, not caller
authorship.

The service records one provisional annotation per observed child correlation, keyed by one
correlation identity, with `origin=host_observed`, `acceptance=pending`, and no child bundle. The
materializer retains the host observation as evidence, and the durable registry exposes its
bounded annotation through lineage status. A
parent may mint an accepted child with `mode=delegate` before spawning; a cooperative child may
self-register with the parent session. Either accepted path binds the
existing annotation and never creates a second record. A stop replay after session rotation must
resolve through the same child/parent-tool aliases; an event with no usable child identity remains
the permanent `missing_subagent_identity` gap and does not also produce an annotation. Parent
advice and frontier delivery are never redirected to the child.

When a retained child stop reports a finding, `subagent_finding_unaddressed` advice names the
registry's annotation ID, or the bound child task ID when available. Advice refresh resolves that
identity through a read-only, parent-scoped lookup; it does not update the annotation's timestamps
or session ownership. The original observation reference remains attached as evidence. If no
unambiguous annotation is available, advice retains that observation reference without inventing
a lineage identity.

Stale-verification advice is scoped to the logical tool call, not to the observed phase. A Codex
`function_call`/`function_call_output` pair, and the hook pre/post pair, share one correlation
identity, so one edit reports one `edit_after_successful_check` finding whose evidence refs name
each observed phase; the originating call's tool name resolves the pair exactly as it already does
for unresolved-command advice (issue #680).
A successful Codex `shell` call is a command outcome, not a check. Deterministic advice moves its
verification baseline only on a current `passed` approved-check fact or an explicit success from a
dedicated verification tool, and a routine read never moves it — including in Detailed mode, where
a routine read keeps its `function_call_output` action and carries no routine marker (issue #681).
Unresolved-command advice still reads every `shell` outcome.

Native child tool callbacks can carry the parent's host session ID together with a child
`agent_id`. A successful delegated `start` preserves the parent mapping: its task result names the
reserved child, but its session and writer still belong to `parent_task_id`. A successful child
attach establishes a separate local route scoped to the host session and child identity. A shared
session without a validated child identity remains an attribution gap. Conflicting aliases or a
callback naming an unbound child also produce a gap; they cannot enqueue parent work or consume
parent advice and frontier notices. Pending lifecycle writes and existing alias ownership are
checked before publishing a child route. A contended write leaves a durable retry for that single
route; replay cannot change its task owner. `SubagentStart` and
`SubagentStop` remain parent lineage signals. Local routes by themselves do not mint cooperative
children or establish registry correlation, and they are excluded from parent session recovery and
parent stream reconciliation.

The #507 source repair adds a Codex bridge for the blessed handle-only flow. A native successful
child `start` callback carries its host subagent identity and bounded returned task, session,
writer, and parent identities. The service validates those fields against the admitted child
mapping and persisted lineage before binding the annotation. A callback that arrives before
`SubagentStart` can establish the same correlation for the later lifecycle hook. Missing or
conflicting identity cannot bind another child. The parent does not need to know an ID before the
host spawns it, and the capability handle is not added to observation payloads. On `PostToolUse`,
`tool_use_id` and `tool_call_id` identify the attach call and must agree when both are supplied;
`parent_tool_call_id` independently identifies the spawn call and may differ. Any explicitly
malformed call identity blocks binding and persists `missing_subagent_identity` in the task
observation ledger, including when the registry sidecar is unavailable. Session-stream
`SubAgentActivity` records follow the same rule: conflicting or malformed parent-call aliases
cannot degrade to a child-only identity, so the `SubagentStart`/`SubagentStop` envelope drops
the child token and records the same durable gap instead of a weaker annotation.

Explicit cooperative correlation fields are validated before consuming the attach handle. A
post-attach transient registry error remains recoverable with the exact request and consumed
handle, including after expiry; a fresh request cannot reuse that capability. A crash after the
child start committed but before handle consumption is recovered the same way: the exact
request finishes consumption after expiry, while any other request is refused as expired. Subagent evidence
under `obs-ledger/1.7.0` distinguishes different parent tool calls. Copies that omit the parent
call may produce separate evidence, while the registry merges only unambiguous aliases. Once a
child-only annotation is bound, a later strong parent-call pair creates a separate annotation:
the shared child token does not prove the call belonged to that previously bound child. Strong
cooperative selectors and advice lookups use the same boundary. A new
observation does not reuse a historical child-only evidence key when the missing discriminator
cannot prove equivalence. Missing or malformed child identity is retained as a durable
`missing_subagent_identity` gap without also creating an annotation for that event.

**2026-09-16 source-only repair boundary (#499/#507):** isolated source fixtures cover the
lifecycle and correlation changes above. No native Codex process, Yoetz workflow, live receipt,
or cooperative-child proof was produced during this repair phase. Fresh installed native
validation with the corrected candidate and the requested independent semantic review remains
required. E-013 and host capability cells are unchanged. The historical native records below
retain their original limits.

### Multi-agent v2 (0.153.4, #754)

Codex `0.153.4` with `multi_agent_version=v2` expresses a delegation in two places, and Yoetz
reads both:

- the **parent** rollout carries `SubAgentActivity` nested in `event_msg` → `item_completed` →
  `item` (never as a `response_item`). Its `agent_thread_id` is the child thread, its `kind` is
  `started` once per delegation and `interacted` for every later exchange, and its `id` is a
  `call_…` token that is not published as `parent_tool_call_id`: only an explicit parent alias
  ever fills that field. `interacted` is an understood kind that names no lifecycle transition,
  so it opens no second annotation and is not an `unsupported_event` gap;
- the **child** rollout's own `session_meta` header carries `thread_source: subagent`, `id` (the
  child thread), `parent_thread_id`, `agent_path`, `agent_nickname`, and `multi_agent_version`.
  Its `session_id` is the *parent* thread, so only `id` is ever the child key. Yoetz maps that
  header to a `SubagentStart` whose `subagent_id` is the child thread; both spellings of the
  spawning thread (`parent_thread_id` and `source.subagent.thread_spawn.parent_thread_id`) must
  agree, and a header naming itself is refused. A header that declares `thread_source: subagent`
  with no usable distinct identity is the one shape that earns `missing_subagent_identity`; an
  ordinary user thread declares no child and earns no gap.

Because the child observes that header in its own session, filing it the ordinary way would name
the child as its own parent. The parent task therefore comes from admitted catalog lineage, never
from the host's `parent_thread_id` token, and the annotation is then bound to the observing child
task — the same shape as the native `PostToolUse` child-start bridge. With no admitted lineage the
observation keeps a bounded `host_lineage_child_not_found` gap instead of inventing attribution.
Both sources produce the same correlation identity for one delegation (the child thread), so a
parent-observed spawn and a child-observed header merge into one annotation rather than two.

`agent_path` and `agent_nickname` are **not** lineage identity. `agent_path` names an agent
definition, not a delegation instance: the same path repeats across every exchange with that agent
and across repeat delegations to it, and its `/root/…` shape is barred from structural payloads by
design. `agent_message` `author`/`recipient` carry the same paths and are treated the same way.

Two record families 0.153.4 adds are admitted as structurally ignored rather than mapped:
`inter_agent_communication_metadata` (already in the vocabulary) and `token_usage_record` (new,
admitted on the structural compatibility profile only). 0.153.4 also added fractional leaves to
ordinary rows — `internal_chat_message_metadata_passthrough.create_time` on every `response_item`
and `rate_limits.*.used_percent` on `token_count`. The canonical value model has no float, so the
parser normalizes such a leaf to `null` in the mapped record and keeps the line; before that, one
fractional leaf refused the whole line as `json_profile_unsupported`. No structural field Yoetz
maps is ever a float, and the line's commitment is still taken over its raw bytes.

**2026-09-16 v2 observation cell (`multi_agent_v2=true`, read-only, not a certification).** Source
`12796f74` (PR #752 head), wheel `yoetz-0.2.1`, pinned test instance `df507f`, Codex `0.153.4`,
originator `Codex Desktop`. Read from the stopped instance's artifacts after the run:

| Fact | Observed |
|---|---|
| Parent rollout record families | `event_msg` 152, `response_item` 118, `token_usage_record` 36, `inter_agent_communication_metadata` 8, `world_state` 3, `session_meta`/`turn_context` 1 each |
| Parent `SubAgentActivity` | 8, all nested in `event_msg.item_completed`; `kind` `started` 2, `interacted` 6; 0 as `response_item` |
| Child rollouts | 2, each `thread_source: subagent` with `parent_thread_id` equal to the parent thread and `session_id` equal to the parent thread |
| Annotations produced | 0 host annotations, 0 aliases, 2 consumed attach handles |
| Parser result at that revision | 174 of 319 parent lines refused (138 `json_profile_unsupported` from the new fractional leaves, 36 `unknown_wrapper_type` from `token_usage_record`) |
| Parser result after this change | 319 of 319 parent lines and 218 of 218 child lines mapped, no per-line reason codes, no `unsupported_event` |
| Stream cursor at stop | event position 77, byte 956,771 of 2,268,686 — the first `SubAgentActivity` is at line 91 |

The 50 `unsupported_event` rows in the parent ledger are exactly the 40 fractional-leaf refusals
plus the 10 `token_usage_record` lines inside those first 77 positions. The run produced no
annotation for two independent reasons: the delegation rows were past the last consumed position
(the hook-driven reconcile stalled, #753), and neither child's own session observed its own
rollout, because the observation cursor and workspace binding for a child thread are established
by that thread's hooks and no hook reached those child sessions (0 observation events, 0 cursors
in both child ledgers).

**Not established by that transcript.** Whether `0.153.4` still emits `SubAgentActivity` with
`multi_agent_v2=false`; whether the v2 `SubagentStart` hook payload carries `subagent_id` (every
hook after 14:39:59Z hit the degraded path, #753); whether `agent_path` is stable across session
rotation; and whether a delegated child thread ever runs the hook bridge, which is what would let
the child-header identity source fire natively. The child-header path is proven against fixture
IMP-015 and the registry bridge, not against a native v2 run. `0.153.4` remains admitted under the
structural compatibility profile: it earns no exact rollout profile and no certified host cell from
this evidence, so its admission stays `partially_understood`.

**Child lanes under the shared root session (#841).** The 2026-09-25 follow-up to #823 ran the
0.3.0 package (`2e9b35ce`, wheel SHA-256 `09e507d3…7588`) on a disposable ADR-028 instance with
Codex Testing `0.153.4`. A parent delegated and one native child attached with the handle,
published, checked, and obtained a receipt. The parent saw that accepted child, but its lineage
view also kept one pending unbound provisional annotation, and one supported `observe reconcile
--session-file <child rollout>` returned `observation_reconcile_failed:mapping_missing` (exit 20).
The raw run evidence is private; only these structural facts are recorded here.

The source analysis names two boundaries, both reproduced from the exact IMP-015 records:

- **Callback identity.** v2 writes the same root `session_id` into every thread of one delegation
  tree (the child header's `session_id` is the parent thread). A delegated child's own callbacks
  therefore arrive under the parent's session. With no host child alias (`subagent_id`,
  `agent_id`, `agent_thread_id`), the child's successful attach `PostToolUse` cannot publish a
  child lane (`start_bind_child_lane_unbound`), so the native child-start bridge never binds the
  annotation, and the child's other callbacks were ordinary parent callbacks.
- **Rollout routing.** A v2 child rollout is named by the child's own thread, never by a host
  session, and an accepted child's route is the digest lane derived from the root session and the
  child thread. Hook-driven stream reconcile skipped every child lane, and manual reconcile only
  matched raw host sessions by filename. The child's own `session_meta` header — the #754
  child-observed delegation signal — could therefore never be delivered natively, and every child
  rollout was refused as `mapping_missing` whether or not a child route existed.

Which callback shape the native run produced is visible in that run's private
`hook_diagnostics.reasons`: `start_bind_child_lane_unbound` on the child's attach means the child
callbacks carried no alias; no child callback diagnostics at all means no hook reached the child
thread. The public issue does not establish which.

The repair keeps every existing authority rule and adds no inference from workspace-wide stream
state or annotation counts:

- A Codex tool, permission, prompt, `Stop`, or compaction callback whose `transcript_path` (or
  `session_file`) is not the session's own rollout reads only that file's first line. When it is
  a safe owner-private `.jsonl` beneath the Codex home's `sessions` root, its v2 header declares a
  delegated child, the filename carries that child's thread, and the header names the callback's
  session as the spawning root, the child thread becomes the callback's host child identity —
  the same fact a native `agent_id` supplies. The attach then publishes the child lane, the native
  child-start bridge binds the annotation, and the child's command, file, and advice evidence
  stays on its own lane. A transcript that proves a delegated child but cannot name it for this
  session, or that contradicts a host alias, keeps the callback an explicit attribution gap with
  the hook diagnostic `child_transcript_identity_conflict`; it never becomes parent work. Session
  lifecycle and `SubagentStart`/`SubagentStop` hooks are never re-attributed.
- A callback routed to a validated host-identity child lane reconciles that child's own rollout
  into the lane. Its header is filed under the parent named by admitted catalog lineage and bound
  to the observing child, so the parent's provisional annotation, the child-header signal, and the
  attach callback merge into one annotation bound to exactly one accepted child.
- `observe reconcile` of a child rollout resolves it through its header to the one lane derived
  from a spawning session bound to that workspace alone and already mapped to a child task. The
  result carries `mode: recovery_child_lane` and shares the automatic cursor, so repeating it
  accepts nothing new. A child rollout that cannot be proven is refused with a bounded reason
  instead of `mapping_missing` (see troubleshooting below). An ordinary unmapped rollout still
  reports `mapping_missing`.

Evidence boundary: a source repair proven by focused unit rows and a composed-READY conformance
row over the exact IMP-015 parent and child records, including idempotent recovery and the
parent's recorded rollup of the child. It assumes the v2 child callback shape the header implies
(root session plus the child's own transcript) and has not been rerun natively. When a child
thread's callbacks carry neither a child alias nor their own transcript, or no hook reaches the
child thread, the child route stays unprovable and recovery reports `child_route_missing`.
`0.153.4` remains admitted on the structural compatibility profile only: this repair earns no
exact rollout profile, formal parity-gate cell, or E-013 capability cell.

Fresh accepted native activity on the current mapped session renews its lease. Delayed delivery
older than 60 seconds, stream history, duplicate replay, predecessor sessions, and terminal host
events do not. A silent child still enters contact loss and the configured recovery window;
source edits by themselves cannot prove continuing contact when observation is unavailable.

The isolated native cell below exercises the parent-minted path for the reviewed legacy
`codex-cli 0.150.1` profile. Broader host profiles need their own execution evidence; host hooks
alone never mint a child.

A fresh project-marketplace carrier requires the supported TUI **Trust all and continue**
ceremony. Before native verification, inspect app-server `hooks/list`: the reviewed carrier has
13 enabled, trusted hooks, including `SubagentStart` and `SubagentStop`, with no warnings or errors.
An active marketplace entry and `features.hooks=true` establish configuration; actual hook
diagnostics and admitted observations establish execution. Keep source-stream coverage separate:
a rollout may contain `spawn_agent` and `wait_agent` records without a usable subagent identity.
Such a stream cannot establish a parent lineage annotation or a late-stop replay by itself.

The 2026-09-07 native cell used source `80d0d94c` and the development `0.1.0` wheel at SHA256
`18b0e5ecd9cc09acb06dd805d90c01dc506241a26e2405b152dec36a51ec6f9d`. All 475 installed package
files matched the wheel. `codex-testing 0.150.1` ran in an isolated home and workspace, with a
trusted project-marketplace carrier, a strict owned MCP route, `multi_agent_v2=false`, and a
synthetic loopback Responses provider. The native process exited `0`; parent start, delegation,
one legacy child, child attach, publication, deterministic check, receipt, and child terminal
completion were recorded. The check and receipt returned structured success with deterministic
coverage and matching child/check frontiers. Parent mapping snapshots remained stable and a
separate child route appeared after attach.

Actual native hooks recorded one `SubagentStart` and one `SubagentStop` with the same child
identity and no gaps on those events. Parent lineage showed one accepted `parent_minted` child and
one provisional `host_observed` annotation. The native payload omitted `parent_tool_call_id`, so
the annotation remained unbound. Two native rollout streams contained 39 and 29 records but no
`SubAgentActivity` or usable child identity fields. Replaying the exact captured streams accepted
zero new records; this proves cursor idempotency, not a newly emitted late stop or session rotation.
The host supplied no structural child-finding outcome, so synthetic advice-policy checks remain
separate from this native evidence.

The native record retains `content_capture_unavailable`, `unpaired_event`, and `unsupported_event`
coverage gaps, plus four bounded `drain_budget_exhausted` diagnostics. A later manual-reconcile
pass on that artifact exposed UUID truncation and left 62 stream rows with `mapping_missing`;
those rows were not native hook or provider failures. The child remained open with contact lost
after host exit, and its recorded receipt did not close work or erase incomplete coverage. This
cell does not prove production model behavior, semantic review, or other Codex profiles.

Manual reconciliation of a native `rollout-*` file requires one full session identity already
bound to the selected workspace, or, for a v2 delegated child's own rollout, one validated child
lane resolved through its header (#841, above). It shares the automatic stream cursor and refuses
an unmapped, ambiguous, or foreign-workspace session as `mapping_missing` before ingesting it. Compressed
`.jsonl.zst` files retain the bounded `unsupported_format` result. Existing truncated aliases are
left visible as legacy recovery gaps; a filename cannot authorize rebinding or deletion of their
pending rows.

The repair was installed from source `931acec4`, wheel SHA256
`1a378d66b71c945a1f2d507f8534f0308265ec7c7fc238f500559539a2956a3a`; all 475 installed package
files matched the artifact. Two public `observe reconcile` calls over the captured parent rollout
returned exit `0`, accepted zero new records, and retained event position 39 and generation 1
without rotation or truncation. The unmapped captured child rollout returned `mapping_missing`.
Full-row digests confirmed all 62 historical pending rows and the four existing bindings remained
unchanged. Before/after file digests also confirmed the original capture and mappings were
unchanged. This installed CLI replay used copied structural state and read-only captured inputs;
it did not launch another native host or service.

The composed READY regression separately exercises parent session/writer rotation, a late
`SubagentStop`, service close/reopen, and a subsequent parent `Stop`. Public lineage and advice
retain the same annotation ID, without a duplicate annotation or changed observation timestamps
from rebuilding advice. This is service conformance evidence; the native cell above did not emit
a child-finding outcome.

The rendered `SessionStart`, `UserPromptSubmit`, tool, and turn-boundary commands bind
`--workspace .`. Codex's hooks contract (re-read 2026-09-03) gives every hook the session `cwd` and
runs command hooks from that directory, so `.` is the current project/subdirectory and the shared
canonicalizer resolves it to the safe Git root. `UserPromptSubmit` must keep that explicit argument:
without it, a fresh unmapped session has no older session binding from which to recover the
workspace and its bounded auto-attach retry stops before a service call.

For supported content-bearing Codex hook events, the native adapter reads the documented
`PostToolUse.tool_response` output and the explicitly linked code/diff fields. The ready service
secret-scans and encrypts selected tool output, changed-file/code, and workspace-diff bytes before
materializing their exact digest/object bindings as `observation_captured` ledger evidence. This is
the source-qualified,
profileless `codex_hook` arm: active observation consent and the exact hook source bind it; no
Claude/Cursor content profile is inferred or accepted. Inspection facts and bounded excerpts
receive separate evidence records. This proves retained byte identity only; it is not an approved
check, artifact verification, independent reproduction, or permission to send the bytes to a model.
Installation, an object header, a successful structural receipt, and a typed MCP response are
separate evidence; none proves that the provider selected native Codex bytes.

The Codex hook capture arm is eligible only for content explicitly linked to the hook event and its
exact task, workspace, host/Yoetz session, source generation, tool-call correlation, multipart set,
object kind, and digest. Codex session-stream records remain outside the native ticket lane and are
excluded from AI-powered review selection. Tool input and path/locator content are excluded from
AI-powered review selection too, although the current Codex hook path may still stage consented
input/locator chunks locally in the bounded encrypted capture lane pending a follow-up staging
filter. Encrypted capture and AI-powered review disclosure have separate authority: selecting these
bytes into a frozen AI-powered review case still requires the effective repository privacy grant and
the independently authorized provider attempt.

For the supported native `hooks observe` path, the hook first closes its local structural envelope,
pairing, lifecycle intent, and outbox state, then presents eligible content to the service-owned
capture lane before the structural FIFO advances. The capture acknowledgement follows durable
encrypted object/manifest and metadata-ticket publication; native content is never copied into the
structural spool. If authenticated staging cannot complete during the bounded pass, a completed hook
records `content_capture_unavailable` alongside the structural record. A host process killed before
that boundary may still leave the honest content gap; after the ticket is durable, a later
structural retry can reuse its fenced manifests without rereading a plaintext spool. Advice
selection remains after drain, and advice is committed only after the host output is emitted.
`SessionEnd` records its lifecycle intent and defers service delivery without rebuilding local
advice, because the closing host cannot receive it. A later hook or the service sweeper drains the
end event and refreshes advice. The encrypted capture-ticket handoff does not change Codex's
historical session-stream path. Session-stream reconciliation remains a separate source and cannot
supply content to a `codex_hook` ticket; session-stream, input, and locator content remain excluded
from AI-powered review selection. The current hook path may still stage consented input/locator
chunks locally pending the follow-up staging filter. Codex keeps its existing replay semantics; the
shared operation-replay, source-generation fencing, and teardown repairs apply to all host adapters.

Legacy synchronous `hooks spool` is a separate structural fast path. It only appends the owner-only
structural spool record and returns; it does not normalize or pair the event, open the service, drain
an outbox, or carry native content. The READY forwarder later consumes the spool and performs normal
service-side normalization, pairing, and forwarding.

Yoetz's own MCP tools fire these same `PreToolUse`/`PostToolUse` hooks, and the hook process that
records them also drains the outbox, so the prescribed start/status/check/respond/receipt workflow
used to feed its own backlog: two rows plus a captured result per `status` read (issue #564). The
ingress now delivers a Yoetz-owned call only as distinct evidence — an explicit host failure or
denial in either phase, or the `PostToolUse` of `start`, `publish_work`, `check`, or `respond` —
and keeps the pre-event of every Yoetz call and the post-event of a non-failed `status`, `receipt`,
or `read_guidance` in the bounded local store only. Yoetz tool input/output is never captured as
content. The same policy applies to the legacy spool replay and to the Codex session stream, so
neither path reintroduces the rows. The shared host-spelling advice guard suppresses pending
frontier or recommendation delivery on a Yoetz-owned hook without an explicit failure. Explicit
self-call failures stay retained, enqueued, and eligible for pending advice. Ordinary tools are
unchanged. To confirm closure converged,
run `yoetz observe drain --workspace . --json` after the agent stops and require
`terminal: drained` with `pending_after: 0`; `retry_pending` names the retryable head cause in
`reasons` (a check barrier's `operation_pending` clears when the check completes), and
`pass_limit` means a producer is still adding rows.

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
  yoetz -- uv --directory /absolute/path/to/yoetz-core run yoetz mcp serve --host codex --semantic off

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

## Smart observation selection (issue #687)

The Codex native hook path applies the shared deterministic observation selector before optional
content extraction and outbox admission. **Focused/standard (512)** is the default. Proven
successful routine reads, searches, and inventory calls may be represented by bounded summaries;
the summary preserves the native identities and source positions it represents. Detailed keeps
eligible routine calls as individual records while the effective pressure state permits it. The
detail mode and the capacity profile are independent choices: `standard` (512), `larger` (2,048),
and `largest` (8,192) are each valid with either mode. The larger profiles have finite provisional
budgets; a selected count is not a sustained-throughput or host-acceptance claim.

Failures, denials, cancellation, interrupted, partial, or unknown outcomes, edits and side effects,
declared checks or negative verification, and reads protected for an obligation, claim, or finding remain
individual. A `PreToolUse` identity is retained before its outcome is known; only a paired,
proven-success read can enter a summary. Ambiguous shell composition, nonzero or conflicting
outcomes, and caller-supplied `routine_read` labels stay protected. Codex session-stream records
remain a separate structural source and are outside this native selection/content path.

Codex states a tool result under `tool_response`, so the proof that a read succeeded is normally
nested rather than a top-level field. The selector and the summary builder share one definition of
that proof; where they disagreed, the buffered account refused every Codex-shaped post and
observation ingestion stopped for the rest of the session (issue #753). A refused summary now
admits its reads individually with a `routine_summary_invalid` coverage gap, names the lane once in
`yoetz observe status`, and records the bounded `routine_summary_invalid` hook reason instead of
the opaque `observe` token. The behavior is shared by every host ingress, not Codex-specific.

Use the owner controls after an exact preview:

```text
yoetz observe selection-preview --workspace /exact/project \
  --detail detailed --capacity larger --session-id <codex-session-id> --json
yoetz observe selection-apply --workspace /exact/project \
  --detail detailed --capacity larger --session-id <codex-session-id> \
  --accept --preview-digest <preview-digest> --json
```

The default is a temporary session override. Use `--persist` on both preview and apply, and omit
`--session-id`, only when the owner wants a workspace default. `--expires-at` is an optional
RFC3339 UTC deadline; `selection-status` reports selected and effective mode/capacity separately.
Revoke with `selection-revoke` at the same scope. Expiry, revoke, lowering capacity, or pressure
affects future optional admission; accepted observations continue to drain and are not rewritten
to fit the new target. Selection does not change Codex hook deadlines, session-stream admission,
content consent, repository privacy, provider, or network authority.

**Configurable capacity (issue #828) — Codex decision.** Codex uses the same local capacity path as
every other host: `yoetz observe selection-preview` with `--capacity standard|larger|largest`,
`--capacity custom --queue-count <64..8192>`, or `--capacity none`, then `selection-apply --accept
--preview-digest`, or the terminal interface's `/observe`. There is no Codex-specific capacity
control, and repository or plugin configuration cannot raise capacity. An agent may relay a change
only after the owner accepts the displayed preview's scope, values, local-hardware consequences,
remaining limits, and lower/pause/resume path; ordinary task permission never authorizes an
increase. `--capacity none` returns `capacity_no_cap_unsupported` because the local state document
has a 16 MiB safety ceiling, and changes nothing; the largest supported capacity is 8,192 rows. MCP
`status` stays read-only for capacity. Hook body caps and Codex hook deadlines are unchanged. Custom
counts need control schema `2.9.0` on both the client and the service; an older revision drops a
saved custom count to the default.

**Lowering above the fallback byte bound (issue #843) — Codex decision.** Codex uses the shared
store path with no Codex-specific behavior. Lowering, revoking, expiring, or ending a larger
selection can leave more accepted rows than the new target holds. Those rows still drain, and the
store keeps finite room, tied only to them, for refused-input loss, delivery attempts, and session
ends. A refused hook reports `hook_observe_degraded: outbox_overflow; loss accounted` only after
the loss is durable. If a `SessionEnd` hook cannot persist its local end, it stays fail-open
within its teardown budget. It prints `hook_observe_degraded: session_end_unrecorded` and
records that reason in `hook_diagnostics.reasons`.

To keep an upcoming read individually linked to a later claim, use the bounded narrowing control
with an active consent and the exact current Codex session:

```text
yoetz observe protect-read --workspace /exact/project \
  --session-id <codex-session-id> --reference obl_<existing-id> --count 1 --json
```

Only `obl_`, `clm_`, and `fnd_` references are accepted. At most 32 logical reads can be outstanding
and the protection expires after ten minutes by default; an explicit expiry cannot exceed that
bound. It grants no content or disclosure authority. Promotion is limited to a native identity
still in the local buffer:

```text
yoetz observe promote --workspace /exact/project \
  --source-identity <source-identity> --json
```

After the buffer has drained, promotion reports `promotion_window_closed` and
`content_availability: not_retained`. Rerun or reacquire current evidence when needed and label
the new observation with its new time and subject state; promotion cannot recover old bytes or
retroactively prove the earlier state.

Codex's profileless native capture arm follows active observation consent; it is separate from
selection. The workspace capture lane has independent limits of 512 staging/pending capture
tickets and 128 MiB of captured content. A larger observation capacity does not raise those
limits. The hook's bounded capture pass and service deadline still apply. If staging is partial,
cancelled, times out, or lacks a complete post/content group, the event remains incomplete and
does not become a successful routine summary; when the hook reaches the recording boundary Yoetz
records `content_capture_unavailable`. A host kill before authenticated staging can leave transient
content without a durable gap. Capture configuration or a successful structural receipt is not
proof that Codex bytes were captured, selected for a check, or used by a provider.

The self-observation exception covers explicit Yoetz MCP workflow names. A Codex shell event such
as `exec_command yoetz observe status`, `yoetz observe selection-status`, `yoetz observe
selection-preview`, or `yoetz closure-prepare` has no authenticated executed-launcher identity in
the hook payload; a path written in the command text is still host input. It therefore remains an
ordinary shell observation and may use the available structural, capture, or materialization
budget. Failures, mutations, and ambiguous shell are never suppressed. This is a current support
boundary for local CLI self-reads; use the explicit MCP status or receipt route where available or
account for the CLI read in coverage and capacity.

### Oversized hook payloads (issue #667)

A Codex hook body over the 256 KiB ingress cap (`MAX_HOOK_STDIN_BYTES`) is refused at stdin,
before any parse. Codex does not use Cursor's 1 MiB identity skim, so the oversized event stays
an unparsed gap. The hook stays fail-open and the host continues. Yoetz records the bounded
`codex_payload_too_large` reason against that event in `yoetz observe status` hook diagnostics,
and notes the `payload_too_large` coverage gap on the consented workspace, where
`yoetz observe status` shows it. That workspace gap does not yet reach task receipts: no row
exists, so a receipt simply has no evidence for the dropped event rather than naming the loss. Before this, the refusal reached the outer handler as the bare
`observe` reason, which named neither the cause nor the affected event, and the native
`post-tool-use`, `user-prompt-submit`, and `session-start` entry points recorded nothing at all
because they parse the body before handing the same bytes to the observation ingress.
The cap is fixed and shared by every host; raising it is not an operator control. Each reader
consumes at most cap-plus-one bytes, so the true size of a refused body is never measured and
never recorded — the bound itself is the whole fact. Nothing about the event is parsed, so the
hook name the host supplied on the command line is the only identity the record can carry: no
tool name, session, or path. The refusal costs exactly that one event; the next ordinary event
still ingests.

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
consumed after terminal completion. A source, manifest, target task/session/writer, profile/version,
mapping, plan, or limit change must produce another preview. Denial, expiry, or a different pending
consent publishes nothing. Import intake never authorizes AI-powered review provider or reviewer
egress.

## 10. Troubleshooting and recovery

| Symptom | Action |
|---|---|
| Target untrusted/unsafe | Correct the explicit root/permissions; there is no force option. |
| Resource invalid | Reinstall from a verified package artifact. |
| Preview stale | Run `status`, then a fresh preview. |
| Modified/partial content | Preserve and review manually; use `replace_modified` deliberately if desired. |
| Compatibility is `unsupported` | Automatic activation is unprofiled; use a supported Yoetz/Codex version pair when capability evidence is required. Session-stream parsing is separate: a newer Codex release is still admitted structurally, and `observe reconcile` reports `admission: partially_understood` with the affected `admission_reasons` rather than `unsupported_format` (issue #656). |
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
| `observe status` shows `mapping_present: false` after a consented `SessionStart` | The hook sends `start mode=create_or_attach` with the canonical `--workspace` root as `workspace_ref` and `codex-session:<session_id>` as `external_ref`. Before automatic new-pair admission, it scans private local lifecycle mappings for an eligible ended same-host session. A unique mapping from a received `SessionEnd`, with every other bound session ended and the candidate bound only to this consented workspace, is selected before the ordinary request: the hook holds the workspace and predecessor lifecycle locks, revalidates ownership and state, and sends one `mode=attach` request carrying that selector plus the new pair. The catalog requires the selected root task to be active and non-quarantined, its canonical workspace and repository-privacy binding to match, and no start already pending for that selected route. Unrelated tasks in the same workspace do not block this recovery (#814); a pair already bound to another task remains a conflict. Delegated child routes require an authenticated attach handle or target selector. Recovery takes a nonblocking workspace reservation before pruning or scanning, then holds it with ordered locks for every eligible ended same-host session through full candidate revalidation, the service RPC, authorized rewrites, and pruning. The revalidation includes unmapped sessions, cross-workspace ownership, mapping identities, and mapping recency; a busy workspace reservation or candidate-lock contention or a changed snapshot returns `auto_attach_recovery_busy` rather than creating work from an unstable selector. A successful recovery rewrites every ended same-host predecessor mapping for that task to the rotated session and writer so pending predecessor rows drain on the successor route rather than being quarantined. With no usable persisted selector, automatic `create_or_attach` admits the new pair as independent work, including beside a dormant task. `workspace_task_exists` identifies only explicit `mode=create` colliding with an identical pair; workspace membership never selects a task. The candidate set is bounded (#549): a recovery unbinds the ended predecessors it consumed, and each `SessionStart` pass keeps at most the 32 most recently mapped ended bindings per workspace, pruning unmapped ended sessions first; a binding is never pruned while its session is live or while a pending or quarantined row still names it, so protected rows may keep the total above 32, ended unmapped rows still terminalize, and a pruned session that resumes re-binds on its next hook event. The public error reveals no selector; a hard crash without `SessionEnd` remains fail-closed rather than being guessed from age. Otherwise read `hook_diagnostics.reasons` for the typed cause: `auto_attach_workspace_unbound` (no paired request was legal), `auto_attach_request_invalid` (an authoring defect — file it), `auto_attach_conflict` / `auto_attach_refused` (the service answered and declined), `auto_attach_result_invalid`, `auto_attach_mapping_write_failed`, `privacy_authority_required`, `vault_locked`, `timeout`, `storage_unsafe` / `storage_corrupt`, `service_incompatible`, or `service_unavailable` (the daemon was still starting; `UserPromptSubmit` and `Stop` retry under the bounded budget; teardown `SessionEnd` records its lifecycle intent and drains without an auto-attach retry). An explicit MCP `start` remains the recovery path; for `vault_locked` on a never-initialized install, that `start` returns the typed `vault_initialization_required` continuation below rather than a dead end. |
| `observe status` shows `mapping_stale` after every resume or compaction | Before issue #578 the `yoetz hooks session-start` status read connected without a workspace locator, so the daemon's repository fence refused every probe as `SESSION_CONFLICT` and a live mapping was reported stale. The rendered command now passes `--workspace .`, and the probe selects its locator in a fixed order (issue #659): an explicit project path other than the bare `.`, then the host payload's session `cwd` (a subdirectory resolves to the repository root), then the hook's own working directory. The host cwd outranks the bare `.` because Codex hook working directories are not stable across surfaces; an explicit path that cannot be canonicalized never falls through to another repository. `yoetz hooks observe --event SessionStart` and the shared mapped-session lane derive the probe locator the same way when no explicit workspace was consented. A fence refusal is `status_workspace_unbound` / `status_workspace_mismatch` with a keep-the-mapping advisory, and a companion diagnostic row names the locator source (`locator_source_explicit`, `locator_source_host_payload`, `locator_source_cwd`, `locator_absent`, or `locator_unresolvable`) so an absent context and a supplied one that failed to resolve are distinguishable; `mapping_stale` means the daemon actually reported the session replaced, and the advisory names the replacement ids. |
| The agent created a sibling task instead of continuing the auto-attached one | The `SessionStart` context names the mapped `session_id` and `writer_id` and says to continue with `start mode=attach` by that session id; guidance and the `start` tool description name the canonical absolute repository root as `workspace_ref`, the value the hook commits (issue #580). The agent's successful scoped `start` re-binds the mapping through `yoetz hooks post-tool-use` from `structuredContent`; a scoped start that binds nothing records `start_bind_unparsed` / `start_bind_invalid_ids` / `start_bind_write_failed`. |
| `observe reconcile` of a child rollout reports `child_parent_unmapped` | The v2 child header names a spawning session that is not bound to the selected workspace alone with a lifecycle mapping. Reconcile or attach the parent session in its own workspace first, or select the workspace that owns it. Nothing was ingested (#841). |
| `observe reconcile` of a child rollout reports `child_route_missing` | The spawning session is mapped, but no validated attach published a child lane for this child thread: the child's attach callback carried neither a host child alias nor its own transcript, or no hook reached the child thread. Read `hook_diagnostics.reasons` for `start_bind_child_lane_unbound`. The provisional annotation stays unbound; nothing is inferred from stream status or annotation counts (#841). |
| `observe reconcile` of a child rollout reports `child_route_ambiguous` or `child_identity_invalid` | More than one route or workspace claims the child thread, or the header declares a delegated child without a usable distinct child and spawning thread. Nothing was ingested; the observation keeps its bounded gap (#841). |
| `hook_diagnostics.reasons` shows `child_transcript_identity_conflict` | A Codex callback's own transcript proved it came from a delegated child that it cannot name for the callback's session, or that contradicts the host child alias. The callback is an explicit attribution gap and is never delivered as parent work (#841). |
| `observe status` shows pending `mapping_missing` after a runtime route conflict | A non-retryable `SESSION_CONFLICT` while acquiring the task runtime keeps the envelope pending for a later drain after its lifecycle mapping is repaired. The route must still pass its ownership checks. Non-retryable conflicts after runtime acquisition remain `ledger_rejected` and enter quarantine. Retryable route conflicts report `service_unavailable` and stay pending. |
| `observe status` shows `ledger_rejected` and `outbox_quarantined` | The service was reachable but rejected one envelope non-retryably. A repeated envelope after a lost acknowledgement, a service restart, or a workflow reattach (a second `start` in the same Codex session) is not such a rejection: its committed operation is resolved task-wide and the row is acknowledged idempotently with no quarantine row. A pending row from an ended host session whose task a successor recovered is delivered on the successor route (`session_superseded` is followed) and is also not `ledger_rejected`. A successor binding that cannot be followed quarantines that row as `session_superseded`, not `mapping_missing`. A `ledger_rejected` row is a genuine conflicting reuse of an event or operation identity. The row is retained under `quarantine_causes`, aggregate `delivery_causes`, and gaps; `pending_delivery_causes` names only rows still in the outbox. Later rows can drain; reclaim only after the underlying defect is understood. A hook-driven attempt also appears in the bounded `hook_diagnostics`, while manual and supervisor drains are represented by status rather than hook activity. Do not restart a ready service. A row is also quarantined after 128 consecutive rejections with the same retryable reason so a catch-all failure cannot block the lane forever; pause, vault, disabled, and designed back-pressure reasons keep their existing recovery behavior. |
| `observe status` exits with `observation_status_failed:<reason>` | The reason names the layer: `workspace_unresolvable` (exit 2) is the locator; `storage_unsafe` (exit 20) is an unsafe state/lock path; `storage_unavailable` (exit 20) is a bounded open, permission, read-only, missing-parent, or lock-acquisition failure; `storage_corrupt` (exit 40) is invalid stored data. The fixed remediation never prints the absolute state path. A sandboxed Codex result proves only that sandbox cell; run and record an unrestricted-terminal comparison separately before making that claim. |

When more than one eligible ended same-host mapping names a different task, automatic admission
records the closed `auto_attach_binding_ambiguous` cause and does not guess or create around the
ambiguous selector. A busy recovery reservation, candidate lock, or changed recovery snapshot uses
`auto_attach_recovery_busy`; `workspace_task_exists` remains the explicit identical-pair conflict.

Busy host lifecycle changes are durable local work. State schema `/11` adds bounded pending
session-lifecycle intents, and a READY or hook drain reconciles them under the workspace and
session reservations before routing their rows; busy mapping writes use an atomic per-session
handoff. Upgrade this state quiescently: stop the older Yoetz service and Codex hooks, install
the new runtime, then restart the service and all Codex integrations before writing `/11` state.
Mixed old and new writers are unsupported because a `/10` writer ignores the new pairing fields and
can erase a deferred intent when it saves.

Setup binds every MCP lookup, registration, verification, and removal to the same explicit
Codex home used for plugin activation. Both `CODEX_HOME` and testing-wrapper
`CODEX_TESTING_HOME` are passed to those subprocesses; the parent environment is unchanged.
Registration and removal preview digests include the selected home, so approval for one home
cannot be replayed against another. An unresolvable explicit home fails before registration.
When activation first exposes the plugin-bundled MCP entry, the interactive wizard shows a fresh
MCP preview and asks for confirmation once before registering the selected launcher. Declining,
a foreign entry, or another intervening change stops the write. Noninteractive callers retain
`preview_stale` and must obtain a fresh preview; old approval is never silently reused.

## 11. Security, privacy, and prohibited actions

Codex is the v0.1 allowlisted first-party client for exact current-chat consent attestation.
It should guide Yoetz setup, installation, and settings changes in normal conversation: explain
each consequential choice, recommend one outcome with its trade-off, and preserve the user's
explicit selection. When the user explicitly wants AI-powered review, recommend Expanded first
for review depth and explain Assisted as the lower-disclosure AI-powered option. Do not silently
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

For a disposable-worktree integration run, use the [Codex dogfood parity runbook](codex-dogfood.md).
The ordinary setup/AI-powered review checks above are necessary but do not prove exact-worktree
activation, consent, host delivery, observation, rollback, or normal-target isolation. In
particular, an isolated Codex home (`CODEX_TESTING_HOME`) does not isolate Yoetz: without
`YOETZ_ISOLATED_ROOT` (ADR-026) exported to every tested process, the run's Yoetz clients and any
service they spawn resolve the normal singleton, state directory, and storage. Prove the mode with
`yoetz service isolation --json` before launch; the parity gate's `service_isolation` facet fails
closed on shared, ambient, or unknown identity.

For the Yoetz-owned external registration, issue #561 makes this propagation a supported product
contract: an isolated preview displays and digest-binds the exact root, apply uses Codex's native
`--env YOETZ_ISOLATED_ROOT=<exact-root>`, and status must report
`isolation_binding=isolated_exact`. Ambient registration stores no environment. A missing or
different known root on a bare registration requires re-registration; arbitrary environment keys, inherited-variable
declarations, and malformed roots classify the same-name entry as foreign and are never replaced.
Before a dogfood model task, use the app-server capture in the parity runbook to launch the real
registered child and satisfy `mcp_child_isolation`; registration status alone is not child-start
evidence.

A pinned test instance (ADR-028, issue #604) is the second guard for the registered child: when the
registration's `command` is the absolute launcher of a runtime created with
`yoetz instance create --bind-runtime`, the child resolves that instance's root even if the `env`
block is lost, and a different root in the environment is refused as `isolation_root_conflict`
rather than obeyed. The `--env` binding, `isolation_binding=isolated_exact`, and the
`mcp_child_isolation` facet remain required and unchanged. The everyday Codex registration must
name the everyday launcher by absolute path: a bare `command = "yoetz"` resolves through `PATH`,
and a test runtime earlier on `PATH` then reaches the everyday endpoint, answers
`service_incompatible`, and its `yoetz service restart` advice supersedes the everyday service —
the failure recorded on issue #604. See [`test-instances.md`](test-instances.md).

Absolute external launchers are managed by the same preview/install/status/remove flow (#654).
Run that flow from the installation you intend Codex to use. Preview selects its console script
from that interpreter's scripts directory and verifies its bytes against the installed Yoetz
RECORD; status recognizes only that exact absolute script, with recognized serving arguments
and the reviewed root. A pinned instance must also have a matching pin and instance identity,
current runtime provenance, and an unexpired lifetime. A basename, an executable bit, a different
installation's matching version, or a path supplied by the host is insufficient.

Review the command and root printed by `integrate codex mcp preview` before applying. An unchanged
absolute registration is a no-op; explicit route changes preserve the launcher; removal previews
name the current absolute command. Drift in the command, launcher evidence, or root invalidates
the preview. Missing or modified launcher evidence, symlink/unsafe paths, conflicting transports,
unexpected environment/arguments, and an absolute command bound to another root remain protected.
If the installed CLI cannot prove its console script, repair that installation before registering.
Symlink aliases and wrapper/module commands are not owned absolute console-script registrations;
use the direct script printed by the installation's preview. Bare/legacy registrations remain
recognizable for removal and explicit migration, without granting ownership to unrelated absolute
binaries. Plugin/external dual ownership remains a blocker. Claude Code and Cursor keep their
existing native-carrier identity rules; this repair changes only Codex external registration.

## Subscription evaluator is a separate Codex role

Codex may be both the host carrying Yoetz and the selected external AI-powered evaluator, but those
are independent cells. Host skill/plugin/MCP activation grants no ChatGPT evaluator login or
privacy authority. Configure the evaluator only through
`yoetz provider codex-subscription setup`; it binds a separate owner-private `CODEX_HOME` and exact
native `0.150.1` app-server cell. Never reuse the host's ambient home, environment, session, tools,
instructions, or repository cwd for the evaluator.

The registered host route still decides whether this Codex process may request AI-powered review
work: `strict` proves zero evaluator launch, while `policy` only permits ADR-009 to decide. Read the
[subscription evaluator runbook](codex-subscription-evaluator.md) before claiming live model use,
runtime isolation, privacy receipt, or cleanup.

Fallback endpoint pairing (issue #582) is host-independent: whether the evaluator or a paired
API provider serves a given attempt is a service-side dispatch decision recorded in provenance
(`fallback_from`), with no Codex-host-specific behaviour, registration, or route input — the
strict/policy route ceiling applies to dispatch authority regardless of which endpoint serves.

Routine/final Codex review budgets (issue #571 item A1) are host-independent too. The service
selects the budget profile from the frozen case: `final` when the frontier carries a completion
claim, `routine` otherwise. It then dispatches with that profile's configured effort and output
limit and records them in provenance. Codex gets no host-specific behavior, registration, route
input, or per-request selector, so the decision for this host is "supported, unchanged".
Recording a completion claim is the only way a check requests the final profile.


### Structural review progress (#571 A2)

Decision: supported through the shared MCP `status` tool with no Codex-specific behavior,
registration, or route input. Call `status` with `view: "operation"` and the check request ID while a long review runs; Codex receives the privacy-minimized text summary, which names the operation state, phase, attempt, condition, elapsed and remaining milliseconds, and the structured page carries every field. The phase vocabulary, deadline, and terminal outcome are
service facts, identical for every host; progress never includes provider text, tokens, reasoning,
or account identity. A read from a different session or writer of the same task may return
retryable `BUNDLE_BUSY` while the check runs. This is shared service behavior, not evidence of a
fresh installed native Codex dogfood run.

### Large tasks and AI-powered review failure recovery (#674–#676)

Codex uses the shared service status snapshot cache and bounded AI-powered review reference
selection. A reduced reference scope reports `semantic_reference_scope_reduced`; it is not full-task
AI-powered review coverage. `failed/case_capacity_exceeded` and `semantic_case_capacity_exceeded`
mean the required packet could not fit before any provider attempt. Select a smaller
claim/obligation scope for a new check. Shorter prose alone need not fix structural capacity.

For `coordinator_failure`, use the check's original request ID with
`yoetz service diagnostics --request-id req_…` to read bounded failure stages. Dispatch entry can
have an unknown outcome, even with null provenance. A missing record is a diagnostic coverage gap,
not proof of non-dispatch. Preserve the original request/attempt identity and existing retry rules.
These shared-path regressions do not certify a fresh installed native-host session.


### Evidence-first closure

The shared guidance instructs this host to paginate `status view=evidence` before publishing
replacement evidence, match state and source identity, and reuse only suitable observed IDs.
Digest-only, unavailable, unselected and clipped items remain separate limits. Evidence discovery
and reuse do not prove native capture coverage or command success. Read `command_attempts` on
obligation rows: this profile may omit command text, in which case reconciliation is `unknown`.
Only service-stamped, explicitly linked observations can support a match or mismatch. The optional
CLI closure composer uses the same projected status inputs; it does not grant capture or egress.
These shared regressions are synthetic contract evidence, not live-host certification.

### Bounded workflow recovery examples (#613)

These examples use the existing MCP operations and selectors on the local Codex cell. Replace
placeholders with values returned by the current call or session-start context; do not reconstruct
them from memory or from the live store.

- **Read retry.** If a `status`, diagnostics, or `status view=operation` read times out, send the
  same read intent with a new read `request_id`. Keep its view, operation filter, cursor, and limit
  unchanged. An unreadable read does not establish that an operation or task is absent.
- **Ambiguous write.** If a `start` response is lost before session/writer IDs are returned,
  replay the exact original `start` body once with the same request ID. Otherwise read
  `status view=operation` with `filter.operation_request_id` set to the original write ID:
  replay only `absent`; use stored `complete`; follow an exact typed continuation and required
  approval before replaying `pending`. Retain and report pending without a continuation,
  `quarantined`, or unknown. Never invent session/writer IDs or create a task to escape a write.
- **Exact-session attach.** When `SessionStart` or recovery context provides a held
  `session_id`, use that exact value as the `mode=attach` selector. Codex's canonical repository
  context supplies the workspace fence; if the request carries identity refs, include the
  canonical `workspace_ref` + `external_ref` pair together, never `workspace_ref` alone. Use the
  returned successor `session_id` and `writer_id`, then read `status` before continuing. A bare
  `task_id` is not an attach selector.
- **Same-pair fresh conversation.** With no held session, call `start mode=create_or_attach` using
  the same exact canonical `workspace_ref` and stable `external_ref` pair, with no `session_id`.
  The same pair resumes; a different complete pair is independent work, even in the same workspace.
  A remote URL is not the workspace identity.
- **Explicit sibling handoff.** Use `start mode=create` only after same-task pair/session recovery
  is exhausted, every earlier write has a known terminal outcome, the binding is healthy and
  authorized, and the user has declared one bounded remaining or repaired verification scope. Keep
  the same canonical `workspace_ref`, choose one different stable `external_ref` such as
  `<work-item>-recovery-v1`, and establish the Codex mapping from the returned task/session/writer.
  Publish a fresh plan, evidence, checks, and receipt for that scope. The handoff must say that the
  predecessor receipt, findings, obligations, evidence IDs, and unresolved status remain separate;
  the sibling cannot make the predecessor look resolved. If no new scope exists, keep the old
  receipt and stop instead of creating another sibling.


## Observation recovery diagnostics

Codex uses the shared selected-admission and outbox store. New native input obeys current hard
pressure and the complete projected buffer/outbox size; accepted replay work remains drainable.
Hard pressure can recover to high before optional detail recovers. A generation-fenced session
end retires pressure scheduling without deleting pending work or historical losses.

Unknown capture inventory can reject new input even when the outbox is empty. The READY
service now schedules a bounded inventory recovery pass independently of the next hook (#695),
using an existing unambiguous mapping and the authoritative catalog/bundle inventory. An unreadable
route or missing mapping stays blocked rather than being assumed empty; recovery retries after
it becomes readable without requiring a fresh event. Do not reset local state, enlarge capacity,
or toggle consent to manufacture a healthy status. Real hard limits continue to apply after
inventory recovery, and previous loss counts and identities remain unchanged.

A known inventory can still hold a stranded capture handoff: a ticket whose `codex_hook` row was
already acknowledged or quarantined, so nothing will consume it. Its age alone used to hold
`oldest_age` at the hard limit with an empty queue (#836). The row's own delivery now retires a
ticket it leaves behind, and the same READY maintenance pass retires any handoff at least 30
seconds old that no queued row can deliver, through the catalog route of the task that owns it
rather than a session mapping. It reports `capture_handoff_retired` or, when an owning bundle cannot
be read, `capture_handoff_unavailable` and retries. A handoff whose row is still queued keeps its
pressure. `observe status --json` names each retirement under `capture_handoff_retirements`
(ticket identity, stage, reason, ticket state, quarantine reason, and age) and records
`content_capture_unavailable`,
because the staged bytes are not attached. In a workspace shared with Claude Code or Cursor, their
hooks deliver queued Codex rows without their own content profile, so a Codex row is no longer
refused as `content_capture_profile_mismatch`.

Recovery emits fixed `capture_inventory_*` reason counts in its internal maintenance summary;
these are not ledger receipts or a new hook diagnostic format. Historical local selection losses with complete original route attribution are reported by
service maintenance even when no later envelope is admitted. New checks reconcile their task's
pending losses first. Each source/session/generation/route lane produces one permanent
`observation_input_loss` marker, preserving all local counts and identities. Missing original
attribution and overflow-only range history remain visible locally rather than being assigned to
an unrelated task. A terminal route drift or quarantined marker operation uses the same task
runtime with a distinct deterministic recovery operation; transient publication failures remain
fail-closed. Catalog scans run through separate read-only worker connections, keeping the service
event loop available during both inventory reads.
Route-valid lane-digest mismatches are reported with the same explicit loss gap and an
unreconciled marker; malformed routes or source identities remain local.
Host-shaped regression tests, including interleaved parent/worker routes and encrypted readback,
are not a version-pinned acceptance run inside the installed vendor application.

Hook and manual drain control failures have `control_` reasons. A protocol error is not a
`ledger_rejected` result. A control failure stops further calls on that connection; the next
hook, manual drain or service sweep can attempt the retained identity. Oversized or locally
invalid requests are terminal. Transport/protocol retries are bounded to 128 consecutive
same-cause attempts; owner-action conditions such as a forbidden method or incompatible
protocol stay pending until corrected. Retrying does not establish whether an earlier request
committed; exact source/cursor identity supplies replay deduplication.

`yoetz observe status --workspace . --json` includes `hook_diagnostics.drain_failures`: up to
32 retained causal records with source/session commitments, generation and position, original
control reason and retryability, adapter stage, final disposition and correlation ID when supplied.
A `control` stage does not identify which side of the socket failed; `request_encode` and
`response_decode` identify locally observed boundaries. Missing correlation remains null. Typed ingest refusals have stage
`typed_ingest` and null control fields.
Diagnostic files rotate at 64 KiB with one backup; this is explicitly incomplete retained history.
A failed diagnostic append records `drain_diagnostic_unavailable` when store bookkeeping succeeds.
No command, prompt, path, raw session ID or raw source ID is included. Existing quarantine is
not automatically replayed or reclaimed, and new diagnostics cannot explain old entries.

Pre-tool hooks reuse advice at its recorded frontier; outcome hooks, lifecycle hooks and service
ingest refresh it. Once structural ingress is durable, ordinary hooks that spent the local
one-second allowance defer optional follow-up with `hook_followup_deferred`. Transient native
content and lifecycle hooks do not take that deferral. Drain snapshot, connect, RPC and local
bookkeeping share one elapsed drain budget; synchronous local writes cannot be interrupted safely.

For shared-store measurements and the remaining native-host coverage boundary, see
[the performance runbook](observation-selection-performance.md).

## Release notifications and upgrade choices

SessionStart can deliver one cached package-update recommendation in this host's context format.
The service checks on READY and at most hourly thereafter, under the existing update-check policy
and 24-hour PyPI cache. Hooks never access the network. Advice remains available when workspace
observation consent is absent or observation is disabled; it grants no observation authority.
Task/receipt advice can occupy the same context slot and defer the recommendation.

Use the exact advertised accept/decline command, including `--release-version`. A new decline skips
that release; older permanent declines remain respected. Acceptance only supplies the upgrade
instructions, and execution requires the user's explicit upgrade request. Package replacement does
not itself prove host activation; a compatible data migration is performed by the fresh service
before READY and must be verified separately. Preserve the existing host roots, ownership and
privacy choices; new settings such as Expanded review require a separate exact approval.

## Recovery directives in errors (ADR-030)

Codex consumes structured MCP results, so the `continuation` token arrives in `safe_details` and the
bounded text projection repeats the resolved directive for parity. No Codex-specific behavior is
configured.

The observed Codex failure this covers: a `start` with `actor_id` `codex:/root` was rejected as
`INVALID_REQUEST` with `fields: ["/actor/actor_id"]`, `reasons: ["invalid_type_or_value"]`, and
no continuation, and the text channel carried neither the field nor a way forward. The same
rejection now carries `input_correction_new_identity` and its text reads
`Rejected: invalid_type_or_value at /actor/actor_id.` followed by the directive to submit the
corrected body once under a new `request_id`. The regression case is
`tests/unit/mcp/test_recovery_directive_delivery.py`, which replays that exact body.

Provider-side failures reaching Codex through the app-server path keep their existing stage-typed
diagnostics (issue #529). Those failures also resolve through the shared recovery registry
(`continuation_for_semantic_outcome`, issue #742): the adapter's closed `failure_class` and the
recorded `semantic_reason` select a frozen directive, and raw provider text never reaches it.
Hook advisories never carry that token; SessionStart's vault-locked advisory appends its own
`vault_unlock_required` token after the host-specific prefix (issue #739). No Codex-specific
recovery wording is configured.

### Compatible newer transcript metadata (0.2.3)

The structural compatibility parser accepts fractional metadata introduced by Codex 0.153.4
without dropping the surrounding message or tool record: unrepresentable numeric leaves become
null, while the record commitment still identifies the original bytes. Its token-usage wrapper
is a known telemetry family from which no work evidence is inferred. Exact certified profiles
are unchanged. This repair does not add multi-agent lineage or promote a newer host capability
cell; unsupported shapes still report their coverage gaps.

For a returned start error with `start_busy_same_identity`, the reservation remains durable and
only its fenced lease was yielded. Replay the exact start body and request ID once, without
inventing session or writer IDs. `start_pending_same_identity` instead means a live lease remains:
wait up to 60 seconds before the one exact replay. If still busy or pending, retain the original
request and report the unresolved start. These continuations do not authorize a new task.

**CLI JSON (issue #741).** When an agent in this host runs `yoetz` in a shell with `--json`, a
CLI-owned JSON error body carries a `recovery` object resolved from the same registry. A workflow
command (`start`, `publish-work`, `check`, `respond`, `status`, `receipt`) also prints JSON when
stdout is not a TTY; its failure keeps the exact wire body on stdout and writes the directive
lines to stderr. This is CLI behavior shared by every host; no Codex-specific behavior is
configured.

## Cold service attachment and recovery (issue #670)

Codex SessionStart is synchronous even when tool hooks use the supported async profile. It
keeps its ten-second registration budget; async tool delivery does not authorize background
startup or service replacement.

For an enabled, consented workspace with no mapped task, auto-attachment now gives the exact
selected service one second to connect or start through its fixed, instance-pinned launcher.
It never supersedes another installation. A compatible stamped holder that is still starting
is reused only while an owner-only nonblocking flock probe confirms that the singleton is held;
an unheld stale stamp is ignored and the fixed launcher makes a normal flock-protected start
attempt. A live incompatible or unknown holder is refused. The connection time counts toward
the existing five-second attachment RPC budget. Turn-boundary retries retain their one-second
outer budget and reserve part of it for the start RPC after a shorter connector arm; ordinary tool hooks and SessionEnd do not start a service. Local-only readiness
probes and unconsented/disabled observation do not take this path.

The native context distinguishes a service that is unavailable or still starting
(`service_unavailable`), an incompatible holder (`service_incompatible`), and an answered
admission conflict (`auto_attach_conflict`). Missing mapping remains explicit. Call cooperative
`start` before material work and follow its exact continuation; a conflict needs an authorized
task selector or explicit admission decision, not a service restart. Successful hook exit alone
does not establish attachment. A unique ended predecessor is attached before a new pair is created;
ambiguous predecessor tasks produce `auto_attach_binding_ambiguous` with a count only, and the
explicit session-plus-new-pair recovery preserves the selected root task even when unrelated
tasks share the canonical workspace (#814, #816). It still checks the active selector, workspace and
repository binding, and pending operations for that task; delegated child routes keep their
authenticated attachment path, and no task interaction authority is added.

Structural pre/post observations remain queued and keep their original identities across
bootstrap. A later successful mapping permits their normal drain. Missing transient content
remains a coverage gap; a recovered queue is not recovered content. Existing host/OS capability
and consent requirements still apply. Automated host-contract tests do not establish native
macOS, Linux, or Windows/WSL 2 acceptance. Native cold-start coverage remains tracked in #670.

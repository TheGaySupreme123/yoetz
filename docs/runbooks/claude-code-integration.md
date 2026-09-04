# Claude Code native integration

This runbook covers exactly one cell: Claude Code CLI `2.1.241` as a local process, project scope,
native marketplace-installed plugin, and an explicit private directory marketplace. It does not
claim that Claude Code consumes Agent Plugins. It also does not transfer proof to Claude Desktop,
remote/web/cloud, synced/managed/user/local scopes, Agent SDK, or headless sessions.

## What Yoetz generates

The managed marketplace source contains:

```text
.claude-plugin/marketplace.json
.yoetz-claude-marketplace-install.json
plugins/yoetz/.claude-plugin/plugin.json
plugins/yoetz/skills/yoetz/SKILL.md
plugins/yoetz/skills/yoetz/references/...
plugins/yoetz/hooks/hooks.json
plugins/yoetz/.mcp.json                 # plugin-managed mode only
```

The marketplace entry is `strict:true`, declares only relative source `./plugins/yoetz`, and does
not redefine plugin components. The plugin manifest is authoritative and sets
`defaultEnabled:false`. Every hook command and the plugin-owned `.mcp.json` entry launch the exact
`yoetz` that rendered the plugin (absolute executable, or `<interpreter> -m yoetz`), recorded in the
source marker; a bare PATH `yoetz` is never written, so the bridge, hooks, and service cannot come
from different installations. Re-render (`update`) after moving or reinstalling Yoetz. Shared skill/reference bytes come from the same packaged sources as the
portable and Codex/Cursor projections. Yoetz writes no credentials, endpoints, user config, ledger,
vault, receipt, or provider state into the plugin, `${CLAUDE_PLUGIN_ROOT}`, or
`${CLAUDE_PLUGIN_DATA}`.

## Explicit roots

Choose an exact trusted project, exact resolved Claude executable, isolated Claude config/cache,
and private marketplace source. The cache must be exactly
`<claude-config-root>/plugins/cache`. The examples use placeholders intentionally; do not infer
them from ambient `$HOME` or a running session.

```text
CLAUDE_PATH=/absolute/path/to/resolved/claude
PROJECT_ROOT=/absolute/path/to/project
CLAUDE_CONFIG_ROOT=/absolute/path/to/claude-config
CACHE_ROOT=/absolute/path/to/claude-config/plugins/cache
MARKETPLACE_ROOT=/absolute/path/to/private/yoetz-marketplace
```

## Preview and install disabled

For plugin-owned strict MCP, preview the exact operation:

```text
yoetz integrate claude plugin preview \
  --claude-path "$CLAUDE_PATH" \
  --claude-config-root "$CLAUDE_CONFIG_ROOT" \
  --cache-root "$CACHE_ROOT" \
  --marketplace-root "$MARKETPLACE_ROOT" \
  --project-root "$PROJECT_ROOT" \
  --mcp-ownership plugin-managed \
  --route-profile strict \
  --action install --json
```

Prepare the returned exact digest through the trusted review lane, then replay the returned request
ID and digest with `plugin install --accept`. `--accept` is not authority by itself; the mutation
also consumes a matching `plugin_artifact_apply` pending and fresh OS-authenticated user presence.
Install admits only exactly proven Claude versions (currently `2.1.241`); a neighboring version stays
explicitly untested. It also refuses foreign/dual/ambiguous MCP ownership, unsafe roots, modified
sources, leftover stage/rollback recovery material, or stale previews.

After install, `status` must show `native_managed`, `marketplace_registered:true`,
`discovered:true`, exact version/cache digest, and `enabled:false`. These prove no loaded session.

## Development activation without the marketplace

For dogfooding or CI, export the exact plugin root and load it for one session; nothing under the
Claude config, marketplace, or cache changes, and no review authority is consumed:

```text
yoetz integrate claude plugin export \
  --output-root "$DEV_ROOT" \
  --mcp-ownership plugin-managed --route-profile strict \
  --development-enabled --json
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_ROOT" "$CLAUDE_PATH" --plugin-dir "$DEV_ROOT" ...
```

`--development-enabled` renders `defaultEnabled:true` (a disabled carrier does not load under
`--plugin-dir`); the tree carries a `.yoetz-claude-plugin-export.json` marker naming that flag, so
status never mistakes it for the marketplace-installed cell, and `preview` refuses it. A development
session proves skill delivery, MCP binding/runtime, hooks, model use, semantic dispatch, and receipts
for the exact bytes, but never marketplace installation, discovery, enablement, or host activation.

An isolated `CLAUDE_CONFIG_DIR` isolates only Claude Code. When the session's Yoetz must not touch
the live install, also export `YOETZ_ISOLATED_ROOT` (ADR-026) into the session environment so the
plugin's MCP bridge, hook commands, and any service they spawn derive config, storage, state, and
endpoints from the isolated root; prove it beforehand with `yoetz service isolation --json` run
under the same environment.

Issue #561 does not add an external-registration mutation path for Claude Code. The supported
Claude development/plugin route continues to inherit the explicitly exported root from the exact
session environment, and its MCP bridge and hooks must be tested under that same environment. No
Codex registration status or `--env` behavior is inferred for Claude Code.

## Upgrading Yoetz under a running service

The local-control handshake pins the exact schema-manifest digest, so after installing a new Yoetz
build the previous build's service still owning the endpoint refuses the new bridge and CLI. The
first plugin tool call (on-demand startup) replaces that service automatically: it asks the stale
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
hosts that use this bridge, not Claude-Code-specific.

## Static and host validation

Run Claude's validator against the exact generated marketplace root:

```text
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_ROOT" "$CLAUDE_PATH" \
  plugin validate "$MARKETPLACE_ROOT" --strict
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_ROOT" "$CLAUDE_PATH" plugin list --json
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_ROOT" "$CLAUDE_PATH" \
  plugin details yoetz@yoetz-local
```

The validator covers the default manifest/hooks/skill paths. Yoetz separately validates exact
`.mcp.json` structure and cache bytes; neither static validator proves MCP connection or model use.
`plugin details` should report one skill, five hooks, and one MCP server in plugin-managed mode.

## Applied-route drift decision (issue #537)

Decision for Claude Code: not supported here — no additional state-root applied-route record
at this time. The plugin-managed `.mcp.json` already binds the route profile and the artifact
digest in its in-tree marker, and the live file reads remain the authority for which route
this host serves; a stale serving process is detected through the existing activation and
ownership read-backs, not through a second record. If a ceiling check ever needs an
applied-vs-serving distinction on this host, that is a separate design-gated change.

## Enable, trust, reload, and activation

Preview `--action enable`, consume a new exact review, then run `plugin enable` with the same
request/digest/roots/options. Project trust, installed state, enabled setting, reload/new session,
loaded plugin root, skill delivery, and model use are different facts. A directory marketplace may
report its source plugin root in session init even though list/cache evidence identifies the copied
install; either root must independently match the rendered bytes. Activation proof additionally
requires the exact installed/discovered/registered/enabled facts alongside the session observation —
a session init alone (for example a development `--plugin-dir` run) proves nothing about the
marketplace-installed delivery profile. Open or restart an exact
Claude project session only after the enable read-back. If updating a running session, use
`/reload-plugins` (and `--force` only when Claude explicitly requires cache invalidation), then prove
the loaded root/digest in that session. Old concurrent sessions may retain the old plugin.

The skill name is `/yoetz:yoetz`. The MCP server is `plugin:yoetz:yoetz`, and callable names are
`mcp__plugin_yoetz_yoetz__<operation>`. A live proof needs a fresh session and correlated
`start`/`status` call through that scoped name; a list/details/MCP handshake alone is insufficient.

Claude Code's generic MCP profile (`yoetz mcp serve` without `--host cursor`) delivers
`structuredContent` for successful tools but only the bounded text `content` for `isError`
results. Cooperative `EVENT_INVALID` therefore cannot rely on `safe_details` reaching the model.
Decision for Claude Code (issue #579): supported here — the text summary names frozen
`reason_code` and JSON-pointer `field` (for example `unsorted_set_field at
/event_drafts/4/payload/obligation_refs`) within the 512-byte bound, with no caller prose. The
public message for `unsorted_set_field`/`duplicate_set_member` states the ascending-ASCII rule.
This is the same token class as the `Repair:` clause (issue #266).

Claim correction uses the shared `publish_work` descriptor and
`publish-work-request/1.1.0`; Claude hooks do not author or infer claim supersession. A session still
bound to the older descriptor/control manifest must be reloaded through the normal plugin/service
upgrade path before that capability is claimed; manifest mismatch stops the stale service before
the new pair can fall through its legacy opaque branch.

## Hooks and observation

Claude Code remains structural-only for issue #302: the scoped hook path discards raw prompt,
result, transcript, path, and error content before storage and therefore cannot mint
`observation_captured` evidence. Any future content-bearing profile is a separate capability and
privacy decision with its own fixture and consent proof.

Claude Code has no `codex exec --json` import surface. Issue #301's bounded import authorization
therefore makes no Claude adapter change; Claude evidence continues through cooperative MCP and
the native hook/observation paths below.

The native hook profile emits only `SessionStart`, scoped-Yoetz `PostToolUse`, scoped-Yoetz
`PostToolUseFailure`, `Stop`, and `SessionEnd`. A bare MCP matcher is a negative control. Hooks call
`yoetz hooks claude-observe` and are best-effort; timeouts/nonzero exits never authorize or block
Claude work. The renderer knows Claude's documented `SubagentStart` / `SubagentStop` stdout shapes,
but this profile does not advertise those events; adding them is a separate profile expansion.

Advice uses Claude Code's documented output contract. `SessionStart`, `PostToolUse`,
`PostToolUseFailure`, and `Stop` may emit `hookSpecificOutput.additionalContext`. The failure event
keeps `hookEventName: PostToolUseFailure` even though Yoetz normalizes its internal advice cadence
to `PostToolUse`. At `Stop`, additional context is Claude Code's non-error feedback channel: it
continues through the same `stop_hook_active` loop guard as a blocking decision, but is labelled as
feedback rather than an error. Yoetz never emits `decision: block` to Claude Code. `SessionEnd`
emits `{}`.

The rendered hook commands bind `--workspace "${CLAUDE_PROJECT_DIR}"`. When a hook ingests
nothing it still exits 0 with `{}`, but records one payload-free `hook_diagnostics` reason that
`yoetz observe status --workspace <project>` reports: `workspace_unresolvable` (the variable was
unset or named a missing, symlinked, or unsafe path), `workspace_unconsented` (the canonical Git
root of that path carries no active consent — note that a `git worktree` is its own Git root, so
consent on the main checkout does not cover it), or `paused`. A successful ingest records no
diagnostic, so read `recent_count` together with the envelopes: no new `claude_hook` envelopes and a
zero `recent_count` after a session that ran Yoetz tools means the hooks never reached the ingress
or the runtime gate is disabled — not that they were dropped for binding.

A consented `SessionStart` auto-attaches a ledger task without an explicit MCP `start`: the hook
sends `start mode=create_or_attach` with the canonical project root as `workspace_ref` and
`claude-session:<session_id>` as `external_ref` (both persisted only as HMAC commitments). Success
shows as `mapping_present: true` in `observe status` and the session's queued rows drain in the
same pass. If that new pair conflicts because the workspace already has a task, the shared hook
path retries once with `mode=attach` only when it already holds a valid private mapping from an
earlier Claude session whose `SessionEnd` was received, every other bound session is ended, and the
candidate is bound only to this consented workspace. The catalog additionally requires one mapped
task, the selector still active, no sibling task, the matching repository-privacy binding, and no
start already pending for that route. This reuses an already-known session selector; the public
conflict still discloses no task or session ID, and a hard crash without `SessionEnd` remains
fail-closed rather than being guessed from age. A successful recovery also rewrites every ended
same-host predecessor mapping for that task to the rotated session and writer. Pending predecessor rows then
drain on that successor route (`session_superseded` is followed, not quarantined as
`ledger_rejected`). A failed attempt records its cause as a
payload-free `hook_diagnostics` reason
(`auto_attach_workspace_unbound`, `auto_attach_request_invalid`, `auto_attach_conflict`,
`auto_attach_refused`, `auto_attach_result_invalid`, `auto_attach_mapping_write_failed`,
`privacy_authority_required`, `service_unavailable`, `vault_locked`, `timeout`, `storage_unsafe`,
or `storage_corrupt`) and the session keeps an observation-only binding; turn-boundary events retry
under the bounded budget. An explicit cooperative MCP `start` bound from its exact `PostToolUse`
result remains the recovery path, not a substitute proof that natural auto-attach works. For
`vault_locked` on a never-initialized install, that explicit `start` returns the typed
`vault_initialization_required` continuation (see the proof checklist) rather than a dead end.

The shared `observe status` CLI maps an unsafe state/lock path to `storage_unsafe`, bounded
open/permission/read-only/missing-parent/lock-acquisition failures to `storage_unavailable`, and
invalid stored data to `storage_corrupt`; other defects retain the internal-error boundary. Its
fixed remediation never prints the absolute state path. A result obtained from a sandboxed Claude
carrier proves only that sandbox cell; unrestricted-terminal behavior needs its own run.

Shared drain terminalization is host-neutral: `ledger_rejected` means the ready service rejected
one envelope non-retryably, so that row is retained in quarantine and later rows proceed. A task
bundle at schema 9 (bundle migration `0009`) stores `claude_hook` rows; schema 8's source CHECK
refused them. The SQLite store now classifies deterministic constraint failures as `ledger_rejected`
(issue #576). Existing task bundles require the explicit [migration procedure](migration-rollback.md);
upgrading or restarting the service alone does not migrate them, and the new writer refuses an
unmigrated bundle before observation ingestion. Migration allows valid pending envelopes to store
unchanged, but delivery still requires a usable session mapping; it does not itself repair a retired
session route or replay quarantined rows. An
idempotent repeat of a committed envelope (lost acknowledgement, service restart, or a workflow
reattach that rotates the mapped Yoetz session) is resolved task-wide and acknowledged, never
quarantined. A pending row from an ended host session whose task was recovered by a successor
session is delivered on the successor route (`session_superseded` is followed). A successor
binding that cannot be followed quarantines that row as `session_superseded`, not
`ledger_rejected` or `mapping_missing`.
A row
also enters quarantine after 128 consecutive rejections with the same retryable reason, except for
designed back-pressure and workspace-global pause/vault/disabled gates. Both cases remain visible
in `quarantine_causes`, aggregate `delivery_causes`, and gaps;
`pending_delivery_causes` names only pending rows. A hook-driven drain also writes
`hook_diagnostics`, while manual and supervisor drains remain visible through status. Neither case
is repaired by restarting a service that already reports ready.

Claude Code's hooks are scoped to Yoetz's own tools, so every `PostToolUse` it observes is Yoetz
observing itself (issue #564). The shared self-observation policy applies: a `PostToolUse` of
`mcp__plugin_yoetz_yoetz__status`, `_receipt`, or `_read_guidance` is ingested into the bounded
local store but not enqueued for delivery; a `PostToolUse` of `_start`, `_publish_work`, `_check`,
or `_respond` enqueues one row; every `PostToolUseFailure` enqueues one row. Claude sends no
`PreToolUse` on this profile, so there is no pre-event to hold back. The `PostToolUse` advice
channel is unchanged by this policy; only outbox delivery is governed. The manual
`yoetz observe drain --json` reports `terminal: drained` once nothing is pending.

Grant observation separately for the exact project. Exercise every advertised event and inspect
`yoetz observe status`. Only consented accepted `claude_hook` envelopes earn coverage. Raw
transcript/prompt/assistant/path/cwd/tool input/tool output/result/error values are discarded before
storage. The exact successful scoped `start` post-hook is the sole routing exception: it validates
the returned task/session/writer identifiers and frontier to bind the Claude session, while storing
none of the response bytes or prose. Confirm `mapping_present: true`, then drain and require accepted
rows before claiming hook coverage. Pause, resume, revoke, deduplication, restart, and gap behavior
require their own evidence.

## Auto mode and host admission

Claude Code's auto-mode classifier sees the tool name, the request JSON, user messages, and
`CLAUDE.md`; descriptions, annotations, and tool results are stripped, so no descriptor wording
can satisfy it. `permissions.allow` / `ask` / `deny` resolve before the classifier and are honored
from the repository's `.claude/settings.local.json` (repository root, resolved through
worktrees to the main checkout); a plugin cannot ship permission rules. Allow rules use the
configured server name: `mcp__yoetz__check` for an external `yoetz` registration and
`mcp__plugin_yoetz_yoetz__check` for the plugin-owned server. (`code.claude.com/docs/en/permissions`,
`/permission-modes`, `/hooks`, re-read 2026-08-30.)

Host admission (issue #467) follows the exclusively observed MCP owner and writes exactly its
`check` name into `permissions.allow` (or `permissions.ask` with `--checkpoint`), digest-bound to
the file bytes. An external route must use the configured server key `yoetz`; a differently named
exact Yoetz route remains visible to ownership status but refuses admission because its callable
permission name is not the fixed supported surface:

```text
yoetz integrate claude admission preview --project-root "$PROJECT_ROOT" \
  --claude-path "$CLAUDE_PATH" --claude-config-root "$CLAUDE_CONFIG_ROOT" \
  --cache-root "$CACHE_ROOT" --marketplace-root "$MARKETPLACE_ROOT" \
  --mcp-ownership <external-registration|plugin-managed> --json
yoetz integrate claude admission grant --project-root "$PROJECT_ROOT" ... --accept --preview-digest <digest>
```

The Claude roots are what the route observation needs (`status` on the plugin); without them
the route is unread and a grant refuses with `host_admission_route_unobserved`. A strict route
refuses with `route_not_policy`; a grant that does not permit review with
`grant_not_permitting`; a service that cannot be read with `grant_unverifiable`. A grant whose
exact entry already sits in the other list is a mode change — `grant` after `grant --checkpoint`
(or the reverse) moves the entry between `allow` and `ask` under the same digest-bound preview;
only re-granting the mode already set is a `noop`. A wider rule
(`mcp__plugin_yoetz_yoetz__*`, `mcp__plugin_yoetz_yoetz`), a deny rule, or the tool in both
`allow` and `ask` is `foreign` and never edited. A mutating preview warns
`host_config_not_compare_and_swap`; keep Claude and other settings writers quiescent during apply.
Yoetz rechecks the exact preimage immediately before mutation and verifies the result, but an
ordinary file cannot exclude a non-cooperating same-UID writer in the final syscall window. If
`.claude/settings.local.json` is tracked in git or `.claude` is a symlink, Claude Code holds its
rules until the folder is trusted; Yoetz itself refuses to edit through the symlink.

Reverse: `admission revoke` removes both exact owner forms; `plugin remove` and a `plugin
install|update` onto the strict route sweep it for `--project-root` and report
`admission_cleanup`; a privacy commit that stops external review sweeps it; a leftover entry
shows as `host_admission_drift` in `provider status`. That report walks from the launch
directory to the repository root, so a subdirectory cwd does not read as `absent`.

The rendered `hooks/hooks.json` carries a sixth hook, `PermissionDenied`, matched to exactly the
external and plugin-owned `check` names. It fires after auto mode (or a rule or another hook)
denies the call and can allow nothing; the ingress keeps only a closed token and records one
payload-free `hook_diagnostics` reason — `host_auto_review_denied` (`source: auto_mode` or
absent) or `host_permission_rule_denied` (`permission_rule` / `hook`) — so `observe status`
can show a held check as host authorization, never as a semantic status. Yoetz deliberately
ships no `PermissionRequest` hook returning `decision: allow`, which would make the plugin the
authority over the host's own review.

## Update

A released plugin byte change requires a new generated manifest version. Preview `--action update`,
consume a fresh exact review, and run `plugin update` with the same roots/ownership/route. Yoetz
rewrites only its exact marker-valid source, invokes marketplace update and qualified project plugin
update, then verifies the new cache/version/digest. New cached bytes are not active until reload or
a new session proves the loaded root. Preserve and report old/orphaned cache roots; do not delete
them merely because Claude normally sweeps them later.

## Disable and remove

Disable is its own preview/review/action and proves only the effective setting. Removal is likewise
preview-bound. It invokes:

```text
claude plugin uninstall yoetz@yoetz-local --scope project --keep-data
claude plugin marketplace remove yoetz-local
```

Then it removes only the exact marker-valid private marketplace source. It preserves plugin data,
Yoetz ledgers, vault/keyring/provider state, privacy/workflow receipts, credentials, other scopes,
foreign marketplaces/MCP entries, modified sources, and orphaned caches. A lost/nonzero CLI result
is `outcome_unknown` — as is any post-mutation state the read-back cannot confirm, even on exit 0;
run status and reconcile rather than guessing rollback. Replacement and removal revalidate the
displaced tree after renaming it out of the public path and destroy only a marker-valid managed
tree; interrupted stage/rollback material surfaces in status as `recovery_required`.

## Proof checklist

Claude Code is not an allowlisted `yoetz consent authorize` attestation client in v0.1. It may show
the agent-safe pending status and direct the user to a supported Codex attestation or local trusted
command, but it must not emulate `vault_initialize` or `vault_passphrase_rotate` authorization.
It still guides setup, installation, and settings choices in normal conversation and leaves each
supported product choice with the user. When semantic review is the stated goal, it recommends
Expanded first and explains Assisted as the lower-disclosure semantic option. It may show the full
v6 repository privacy preview, but its missing chat-authority capability is a technical boundary:
give the shortest exact trusted-local continuation and never silently downgrade the chosen recipe,
provider, or model.

First-run start continuation (issue #512): on a never-initialized install, the agent's first MCP
`start` returns non-retryable `VAULT_LOCKED` carrying
`safe_details.continuation: vault_initialization_required`. The Claude Code flow is trusted-local:
run `yoetz consent prepare vault_initialize`, show the returned danger text to the user, direct
them to run `yoetz consent review` on a local terminal (the `authorize_command` the generic
profile advertises is valid only for an allowlisted agent-chat client, which Claude Code is not),
wait for the ceremony's terminal result, then replay the exact original `start` request ID and
body once. On denial or expiry the agent states the boundary and continues without Yoetz.

Record source/render/marketplace/cache/executable digests, exact Claude version/OS/architecture,
scope, settings state, component inventory, enabled state, loaded root and session boundary, MCP
owner/source/runtime, scoped model call, hook consent/evidence, semantic/provider attempt and privacy
receipt, and final workflow receipt as separate cells. Never summarize those cells as one “plugin
works” flag.

## Codex subscription evaluator from Claude Code

A Claude Code policy route may request the same service-owned
`codex-chatgpt-subscription@1` semantic evaluator. Claude never receives the Codex OAuth credential,
dedicated home, app-server handle, or tool authority, and Claude activation/model use is not proof
that the evaluator ran. A strict Claude route must produce `route_semantic_ceiling` with zero Codex
child launch. Use the [subscription evaluator runbook](codex-subscription-evaluator.md) and record
Claude host activation, semantic attempt/runtime evidence, privacy receipt, corrective influence,
and workflow receipt as separate claims.

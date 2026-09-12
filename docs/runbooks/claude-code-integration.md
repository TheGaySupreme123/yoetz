# Claude Code native integration

## Conditional agent guidance

The skill keeps its activation boundary, core workflow, and safety floor in the entrypoint.
Read workflow guidance before `start`, publication policy before `publish_work`, and coverage
and receipts before `check`. Setup/consent, vault/credential operations, transcript import, and
recommendation decisions route to the corresponding sections of request templates only when
needed. Already-read guidance need not be fetched again while present in context.

Use `semantic_if_configured` only when semantic review is known to be optional; omit `mode` when
relying on the configured default. Select `semantic_required` for an explicit user requirement,
effective policy, or named acceptance criterion requiring independent semantic judgment. Preserve
required review and all host/disclosure approval boundaries. Installed guidance bytes alone prove
neither activation nor semantic dispatch.

The native plugin selects `skills/claude-code/yoetz/SKILL.md`. Its entrypoint names
`/yoetz:yoetz`, uses the declared MCP namespace, reads installed references on demand, and refreshes
Yoetz status after Claude resume or compaction. `SessionStart` additional context is a bounded cue;
replayed session context does not establish the current ledger frontier. Plugin replacement uses
Claude's documented `/reload-plugins` or a new session, followed by loaded-root and digest checks.

A new Claude session reads guidance and discovers tool schemas before calling MCP `start` as
its first workflow operation, before substantive research, commands, edits, or delegation.
Guidance reads (including `read_guidance`), discovery commands, and necessary bootstrap clarification
remain permitted. Hook auto-attachment is a cue, not proof of a current-scope plan: mapped
SessionStart context directs cooperative `start mode=attach` before `status` with the returned
ids; unmapped context directs `start` before material work. Same-session compaction uses held
current ids for `status`. Both startup messages route failures through exact continuations,
same-request recovery, and a named one-time repair before a blocked-startup user handoff.
A first non-retryable failure alone does not permit continuing without Yoetz; see
[startup failure precedence](../../guidance/coverage-and-receipts.md#startup-failure-precedence).
Claude documents PreToolUse `permissionDecision: deny` and exit 2, but this integration does not
ship an owner-selected required-startup deny gate; instruction delivery is not enforcement.

Design basis, checked 2026-09-09: Claude's [skills guidance](https://code.claude.com/docs/en/skills)
recommends a use-case-first description and concise instructions with supporting references.
Yoetz keeps this workflow in the conversation because a forked skill lacks that conversation's
history. The [plugin guide](https://code.claude.com/docs/en/plugins) supplies the namespaced
invocation; [hooks](https://code.claude.com/docs/en/hooks) and
[sessions](https://code.claude.com/docs/en/sessions) explain the reload/resume context. Yoetz does
not add `allowed-tools`, an automatic fork, or a new hook to personalize prose. The five shared
references still own evidence, consent, and receipt semantics. These sources justify instruction
design, not a new tested Claude version or native behavioral acceptance result.

Anthropic's own [example skill](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/example-plugin/skills/example-skill/SKILL.md)
and [skill-development skill](https://github.com/anthropics/claude-code/blob/main/plugins/plugin-dev/skills/skill-development/SKILL.md)
use focused activation descriptions, imperative procedures, and links to supporting files. The
Claude Yoetz entrypoint follows those patterns and addresses Claude directly; it does not ask the
agent to identify which host is reading it or carry another host's restart instructions.


This runbook covers exactly one cell: Claude Code CLI `2.1.241` as a local process, project scope,
native marketplace-installed plugin, and an explicit private directory marketplace. It does not
claim that Claude Code consumes Agent Plugins. It also does not transfer proof to Claude Desktop,
remote/web/cloud, synced/managed/user/local scopes, Agent SDK, or headless sessions.

## What Yoetz generates

Claude Code keeps the native carrier's marker-bound launcher identity described below. The
installed-RECORD proof added for Codex external registration in #654 does not classify Claude
sources or establish Claude activation; use this host's source-precedence and native-plugin checks.

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
from different installations. Re-render (`update`) after moving or reinstalling Yoetz. The native
entrypoint and five shared references come from their canonical packaged sources; the references
are byte-identical across host projections. Yoetz writes no credentials, endpoints, user config, ledger,
vault, receipt, or provider state into the plugin, `${CLAUDE_PLUGIN_ROOT}`, or
`${CLAUDE_PLUGIN_DATA}`.

The plugin-owned `.mcp.json` starts the bridge with the explicit `--host claude` identity (and
`--semantic off` for the strict route). This identifies the serving carrier for diagnostics; it
does not grant host admission or agent-chat attestation.

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
Install admits any Claude version at or above `2.1.233`, where the plugin and hook surfaces exist,
and reports `host.version_provenance` on the preview (`host_version_provenance` on status):
`tested` for the exactly proven `2.1.241` cell, `untested` for any other admitted version
(issue #656). An `untested` host runs the same artifact but earns no proven cell; a version below
the floor is refused as `format_unsupported`. Hook ingress keeps the same distinction: an unknown
`claude_code_version` is admitted as `untested` under the conservative paired contract, never as
the proven profile. It also refuses foreign/dual/ambiguous MCP ownership, unsafe roots, modified
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

Issue #604 (ADR-028) adds the executable-bound guard and, again, no mutation path. When the plugin's
hook and MCP commands name the absolute launcher of a pinned test instance (one provisioned with
`yoetz instance create --bind-runtime`, which `scripts/provision_test_instance.py` always does),
that launcher resolves the instance's own root even if Claude Code launches it without the exported
variable, and refuses a conflicting variable (`isolation_root_conflict`). The everyday plugin
installation must name the everyday launcher by absolute path, never a bare `yoetz` resolved
through `PATH`, so that a test runtime earlier on `PATH` cannot become the host's Yoetz. Verify with
`yoetz instance status --json` from the exact launcher the plugin names: `binding` must be
`runtime_pin` or `environment_and_pin` for a test instance and `ambient` for the everyday install.
See [`test-instances.md`](test-instances.md).

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

Decision for Claude Code: no Codex applied-route record is used here. The explicit `--host claude`
identity prevents the Codex-only drift comparison from being applied to this host. The
plugin-managed `.mcp.json` still binds the route profile and artifact digest in its in-tree marker,
and live file reads remain the authority for which route this host serves; activation and ownership
read-backs cover stale plugin processes. A generic legacy `.mcp.json` remains readable during
upgrade/remove but is unproven until the plugin is re-rendered.

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

Claude Code's native MCP profile (`yoetz mcp serve --host claude`) delivers
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
`observation_captured` evidence. Content-bearing profiles are separate capability and privacy
decisions with their own fixture and consent proof; the explicitly opted-in profile below is the
ordinary-work decision for this release.

The default structural artifact keeps that boundary. An explicitly rendered ordinary-work
artifact uses the neutral profile id `claude-code-ordinary-observation-v1` and subscribes to
Claude's generic `PreToolUse`, `PostToolUse`, and `PostToolUseFailure` events plus lifecycle and
permission/API-failure signals. It does not subscribe to `FileChanged` or `PostToolBatch` until a
deduplication contract proves those signals add distinct work. The hook command carries the exact
profile id with `--observation-profile`; the id is a mapping contract, not a claim about the
installed Claude version.

The [Claude hook contract](https://code.claude.com/docs/en/hooks) keeps permission
requests separate from tool execution. `PermissionRequest` has no tool-call identifier, so Yoetz
retains an uncorrelated permission event without inventing a tool action. `PermissionDenied`
reports auto-mode refusals; it does not cover manual dialog denial, deny rules, or a pre-tool hook
blocking execution. `StopFailure` records an API-failed turn without ending the observed session,
and emits no advice output. The ordinary Claude mapping is
`claude-code-hooks-ordinary-v2`: Claude's `PostToolUse` event is an explicit host-tool success
fact, so a successful `Read`, `Bash`, or other tool result is recorded as success even when the
native result has no exit field. Yoetz never fabricates `exit_status: 0`; an exit status is retained
only when Claude supplies one. `PostToolUseFailure`, denial, interruption, error, invalid or
unknown host status, and conflicting host fields override that event-level success. MCP result
content is domain data: only its outer `isError`/`is_error` signal is an execution outcome; nested
`status`, `outcome`, `success`, and exit-like fields cannot override Claude's host event.
A background Bash launch
is recorded as partial until the host supplies completion evidence. These decisions do not add
filesystem or batch observation.

Select these hooks with `--observation-profile ordinary` on the existing Claude plugin
preview/install/update/status commands, or on `yoetz integrate claude plugin export` for a
development directory. Repeat the same profile when applying an exact preview. To return to
scoped structural hooks, preview and apply an update with `--observation-profile structural`.
Artifact selection does not grant content capture.
Preview names the selected profile. Status reports the requested profile and confirms an installed
profile only when its verified marker and artifact digest match; otherwise that installed value is
unknown rather than inferred from the request.

Native content remains a second, per-host consent arm. After granting structural observation, an
operator can enable and later revoke it with the user-facing commands below:

```text
yoetz observe content-enable --workspace /exact/project \
  --profile claude-code-ordinary-observation-v1
yoetz observe content-status --workspace /exact/project --json
yoetz observe content-disable --workspace /exact/project \
  --profile claude-code-ordinary-observation-v1
```

The service accepts Claude chunks only when that exact profile is active in local consent and in
the mapped task grant. A missing or mismatched profile drops plaintext chunks and records
`content_capture_unavailable`; chunks are never retained in the structural outbox for later
replay. An authorized native hook reserves the workspace drain before enqueueing its structural
row, keeping a background sweep from consuming that row before the foreground content attempt.
The reservation is nonblocking and is released on cancellation or after the bounded drain; a busy
owner can still leave an explicit content gap. The hook commits its structural envelope, pairing,
mapping, and outbox intent locally
before attempting the bounded service drain. Teardown `SessionEnd` has no service drain:
its local lifecycle and outbox intent are durable before the
hook returns, and a later hook or the service sweeper retries delivery. With no later hook, a
ready service's idle sweep interval is 60 seconds. Content-bearing
ordinary-profile events retain a one-second drain window; contentless structural rows defer
service delivery. When chunks exist, the pass prioritizes the current row after its same-session
FIFO prefix, within that one-second drain
and sixteen-row bound. Teardown keeps its host-clamped three-second hook and skips local advice
construction because the closing host cannot receive it. A healthy accepted drain forwards the
transient chunks and exact profile to the service; a bounded service failure or completed
cancellation path leaves the
structural record plus an explicit content gap.
A hard process kill or service failure before authenticated service-side staging completes may
lose transient content without a durable gap marker; there is no plaintext local spool or offline
acceptance guarantee. For this ordinary Claude profile, the capture-only service request can commit
encrypted objects, manifests, and a metadata-only capture ticket before the structural FIFO ingest.
After that boundary, a retry revalidates the original host/source and content-authority generations,
requires the complete expected group/part set, and reuses the ticket rather than reminting content.
The bounded staging handoff can therefore survive a service restart, while a revoked or incomplete
ticket remains an honest content gap. The current installed Claude `2.1.261` probe is a candidate
host fact only; it does not certify this ordinary profile without an exact isolated fixture and a
receipt that separately proves native hook delivery, accepted content, semantic selection, and any
resulting influence.
Native semantic selection uses the accepted tool event's durable session route and does not
require an approved-check policy. Local capture consent and repository disclosure permission
remain separate requirements.

Claude Code has no `codex exec --json` import surface. Issue #301's bounded import authorization
therefore makes no Claude adapter change; Claude evidence continues through cooperative MCP and
the native hook/observation paths below.

Cursor's explicit-start repair (issue #661) leaves Claude's binder and hook subscription unchanged.
[Claude's hooks reference](https://code.claude.com/docs/en/hooks#posttooluse), checked 2026-09-08,
specifies `tool_response` for successful `PostToolUse`. Claude continues to pass that field from
its exact Yoetz-scoped tool to the shared binder; Cursor's `result_json` / `tool_output` and
server-key normalization are confined to the Cursor adapter. Cross-host regression checks cover
Claude's structured result, single JSON text block, live-characterized JSON string, and failed
start rejection.

The native hook profile emits `SessionStart`, scoped-Yoetz `PostToolUse`, scoped-Yoetz
`PostToolUseFailure`, `Stop`, `SessionEnd`, `SubagentStart`, and `SubagentStop`. A bare MCP matcher is
a negative control. Hooks call `yoetz hooks claude-observe` and are best-effort; timeouts/nonzero
exits never authorize or block Claude work. The child hooks retain only the bounded child identity;
transcript, prompt, agent type, and path data stay outside the Yoetz envelope.

The renderer uses a lightweight entrypoint that avoids loading the full CLI application graph.
Ordinary events have a five-second budget, `SessionStart` and `Stop` ten seconds, and teardown
`SessionEnd` three seconds. Structural capture and pairing close before service drain, while
advice-bearing events remain synchronous so Claude receives `additionalContext` in the same hook
response. The conservative 0.2 profile used five lifecycle events; the 0.3 capability cell adds
`SubagentStart` and `SubagentStop` only where the exact installed cell and evidence below support
them.

### Task-tool subagents and attribution (#506)

The exact pinned capability cell remains `claude-code-cli-local-project-2.1.241`. The earlier
installed Claude Code `2.1.261` fixture proves native child-hook delivery in an evidence-only
plugin. The latest installed binary reported `2.1.263`; it was exercised in a fresh isolated
strict-plugin export with a bounded loopback Messages provider. Claude's real native `Agent` path
started and completed one child at `spawn_depth=1`; both `SubagentStart` and `SubagentStop` hooks
ran successfully. The observed start shape carried `agent_id`, `agent_type`, `session_id`, `cwd`,
and `transcript_path`; stop additionally carried `agent_transcript_path` and `permission_mode`.
Neither event carried a parent tool-call id, so child-only correlation is a required supported input
shape.

The final exact-wheel cell used `yoetz-0.1.0-py3-none-any.whl` at SHA256
`8d54a73c87e5e49b0b6179ad58f673d2b2f40b1a93c80c4393ce56ae36e82988` from source commit
`2e8b0b48`, with 216 packaged resources (`sha256:1e4cc667456c1cf9ac579d7bc1db186937c29abd9637025c42569ffc7701895c`)
and a strict plugin export (`sha256:f2f7249ef47e43cde1fb0004d2bb770125665c7fc15664c8c6d116242dec6fdb`) loaded
through Claude's development `--plugin-dir` carrier in a fresh mode-0700 isolated root; this is not
marketplace-installed activation. It completed a Yoetz parent `start`, `mode=delegate`, native
child `Agent`, child `mode=attach`, one `publish_work`, deterministic `check`, and JSON `receipt`;
the child receipt was `rcp_f567225d-a34f-4a07-beda-2866649a3b57` with digest
`sha256:47a578bbdaedd7f93e3389ee7d212b84feb132dd44ff2f02a1c8d10e63ba3e3a` and the recorded
conclusion `insufficient_coverage` (`semantic_review_not_requested`). The parent and child ledgers
durably recorded delegation, the child action, check, and receipt. The provider remained a loopback
synthetic Messages server, Claude auth status was `loggedIn=false`/`authMethod=none`, and no normal
credentials or user vault material entered the root; only a throwaway synthetic passphrase vault was
initialized inside that isolated root. Workspace-level observation consent covered only the
synthetic workspace. Post-run status
recorded `claude_hook:true` but `mapping_present:false`, ten `mapping_missing` quarantines, and
`outbox_quarantined`/`unpaired_event` gaps, so this cell proves hook execution but does not claim
attributed host observation or accepted hook coverage. The first `SessionStart` hook was cancelled;
child and operation hooks returned success. The parent's advice/frontier remained independent and
recommended `refresh_observation`. This proves the bounded host and Yoetz workflow under the
recorded limits; it does not promote either installed version to the pinned `2.1.241` capability
cell or prove production model use.

For a validated child signal, the service normalizes `agent_id` to a bounded
subagent identity and retains only structural correlation. `origin=host_observed` and
`acceptance=pending` stay service-stamped until an accepted `mode=delegate` handle or cooperative
self-registration binds the same correlation. A hook carrying only the parent's `session_id`, or a
PostToolUse row fired inside a child without a validated child identity, is an attribution gap and
never parent work. The parent's advice and frontier lane remains independent. Transcript paths,
prompts, agent types, and summaries are never correlation proof.

Shared-session callbacks preserve the parent mapping when the child attaches. A successful child
start can establish a separate local route when Claude supplies a validated child agent ID. If the
callback omits that identity, names a foreign task or service session without a matching child
route, or supplies conflicting aliases, it remains an explicit attribution gap. Such a callback
cannot enqueue parent work or consume parent advice and frontier notices. A local delivery route
does not create or accept a cooperative child. Ordinary child callbacks with no safe identity
remain outside the attributed delivery contract.

This decision is based on installed native execution and the pinned profile boundary; a current
online Claude reference or a renderer fixture does not upgrade the pinned cell. The #509 host
matrix records the `2.1.261` child-hook fixture and the later installed `2.1.263` bounded
parent/delegate publication, check, and receipt run as separate evidence cells. Cooperative MCP
self-registration remains a separate, explicitly bounded path.

The historical 0.3 child-hook cell used control 2.5, which admits the `pairing_mode` and
`correlation_kind` metadata emitted by Claude ingress. Current main's control 2.6 successor retains
those fields and adds the bounded observation-selection projection; both peers must use the same
current manifest after integration. Earlier development artifacts omitted the pairing fields from
the closed schema: the client returned `frame_invalid` before sending the observation, and the hook
layer reported `ledger_rejected`. That refusal did not establish a service-stage failure or an
unsupported host signal. The frozen control 2.4 schema remains available for historical validation.
Verify admitted observations separately from successful cooperative tool calls and local queue
drainage.

The 2026-09-07 native cell used source `80d0d94c` and the development `0.1.0` wheel at SHA256
`18b0e5ecd9cc09acb06dd805d90c01dc506241a26e2405b152dec36a51ec6f9d`. All 475 installed package
files matched the wheel. Claude Code `2.1.263` ran with an isolated synthetic workspace and
passphrase vault, the development `--plugin-dir` carrier, a strict MCP route, and a synthetic
loopback Messages provider. The native host and driver exited `0` without timeout. Parent attach,
delegation, native `Agent`, child attach, publication, deterministic check, and receipt completed;
request/result models, child identity, operation ordering, and receipt/check bindings validated.
`SessionStart`, `SubagentStart`, `SubagentStop`, and `Stop` hooks succeeded without cancellation.

Consented hook envelopes retained one validated child identity across `SubagentStart`, four child
`PostToolUse` callbacks, and `SubagentStop`. The four child callbacks used a separate scoped route;
five mapping snapshots preserved the parent identity. Child frontier delivery and advice state
remained separate from the parent, and the parent lineage contained an annotation. Native output
stream records omitted the child identity fields, so this attribution is supported by the installed
hook ingress and retained structural state, not independently attested by the output stream.

Final observation status showed Claude hook coverage, zero pending rows, zero quarantines, and a
completed drain. One bounded `drain_budget_exhausted` diagnostic remained; a historical parent
`mapping_missing` event did not recur in child callbacks. Public status retained
`content_capture_unavailable`. The check and receipt used deterministic coverage with
`semantic_review_not_requested`; their validation allowed later observation-related frontiers but
did not independently prove the intervening digest chain. This cell proves the bounded native
workflow and lane separation. Production model behavior, semantic advice content, marketplace
activation, and support for the pinned `2.1.241` capability cell remain outside its coverage.

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
same pass. Before admitting a new pair, the shared hook path checks private persisted mappings
from eligible ended Claude sessions. Eligibility requires a received `SessionEnd`, every other bound
session ended, and a candidate bound only to this consented workspace. A unique eligible mapping is
selected before automatic new-pair admission: the hook holds the workspace and lifecycle locks,
revalidates ownership and state, and sends one `mode=attach` request carrying that selector plus the
new pair. The catalog requires one
mapped task, the selector still active, no sibling task, the matching repository-privacy binding,
and no start already pending for that route. Recovery revalidates unmapped sessions,
cross-workspace ownership, mapping identity, and mapping recency; a busy workspace reservation
defers with `auto_attach_recovery_busy`, while candidate-lock contention or changed state returns
the closed `auto_attach_recovery_busy` boundary rather than creating work from an unstable selector.
A successful recovery
rewrites every ended same-host predecessor mapping for that task to the rotated session and writer.
With no usable persisted selector, automatic `create_or_attach` admits the new pair as independent
work, including beside a dormant task. Pending predecessor rows then drain on that successor route
(`session_superseded` is followed, not quarantined as `ledger_rejected`). `workspace_task_exists`
identifies only explicit `mode=create` colliding with an identical pair; workspace membership never
selects a task. Age alone never proves a host session ended. A failed attempt records its cause as a
payload-free `hook_diagnostics` reason
(`auto_attach_workspace_unbound`, `auto_attach_request_invalid`, `auto_attach_binding_ambiguous`,
`auto_attach_conflict`,
`auto_attach_refused`, `auto_attach_result_invalid`, `auto_attach_mapping_write_failed`,
`privacy_authority_required`, `service_unavailable`, `vault_locked`, `timeout`, `storage_unsafe`,
or `storage_corrupt`) and the session keeps an observation-only binding; `UserPromptSubmit` and
`Stop` retry under the bounded budget, while teardown `SessionEnd` records its lifecycle intent and
defers service delivery without spending an auto-attach retry. An explicit cooperative MCP `start`
bound from its exact `PostToolUse` result remains the recovery path, not a substitute proof that
natural auto-attach works. For
`vault_locked` on a never-initialized install, that explicit `start` returns the typed
`vault_initialization_required` continuation (see the proof checklist) rather than a dead end.

Busy host lifecycle changes are durable local work. State schema `/11` adds bounded pending
session-lifecycle intents, and a READY or hook drain reconciles them under the workspace and
session reservations before routing their rows; busy mapping writes use an atomic per-session
handoff. Upgrade this state quiescently: stop the older Yoetz service and Claude Code hooks,
install the new runtime, then restart the service and all Claude Code integrations before writing
`/11` state. Mixed old and new writers are unsupported because a `/10` writer ignores the new
pairing fields and can erase a deferred intent when it saves.

The shared `observe status` CLI maps an unsafe state/lock path to `storage_unsafe`, bounded
open/permission/read-only/missing-parent/lock-acquisition failures to `storage_unavailable`, and
invalid stored data to `storage_corrupt`; other defects retain the internal-error boundary. Its
fixed remediation never prints the absolute state path. A result obtained from a sandboxed Claude
carrier proves only that sandbox cell; unrestricted-terminal behavior needs its own run.

Shared drain terminalization is host-neutral: `ledger_rejected` means the ready service rejected
one envelope non-retryably, so that row is retained in quarantine and later rows proceed. A task
bundle at schema 9 (bundle migration `0009`) stores `claude_hook` rows; schema 8's source CHECK
refused them. The SQLite store now classifies deterministic constraint failures as `ledger_rejected`
(issue #576). During a compatible 0.2-to-0.3 package update, after old writers are stopped, the
fresh service runs its backup-first bundle migration before READY; no per-task migration ceremony
is required. The ordinary writer still refuses an unmigrated bundle before observation ingestion,
and an unsupported or ambiguous startup result remains fail-closed with the [migration and rollback
procedure](migration-rollback.md). Migration allows valid pending envelopes to store unchanged, but
delivery still requires a usable session mapping; it does not itself repair a retired session route
or replay quarantined rows. An
idempotent repeat of a committed envelope (lost acknowledgement, service restart, or a workflow
reattach that rotates the mapped Yoetz session) is resolved task-wide and acknowledged, never
quarantined. A pending row from an ended host session whose task was recovered by a successor
session is delivered on the successor route (`session_superseded` is followed). A successor
binding that cannot be followed quarantines that row as `session_superseded`, not
`ledger_rejected` or `mapping_missing`.
A `SESSION_CONFLICT` while acquiring the task runtime reports `mapping_missing`, keeping the
envelope pending for a later drain after its lifecycle mapping is repaired. The route must still
pass its ownership checks. Non-retryable conflicts after runtime acquisition remain
`ledger_rejected` and enter quarantine.
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
`PreToolUse` on this profile, so its reviewed pairing contract is post-only and there is no
pre-event to hold back. Its `tool_use_id`, when present, identifies the observed result; no
missing-pre gap is created for a legacy post-only hook. The `PostToolUse` advice
guard recognizes Claude's plugin spelling together with the other host spellings, so a self-owned
hook does not lease pending frontier or recommendation context for the call being observed.
Explicit self-call failures remain retained and enqueued. The manual
`yoetz observe drain --json` reports `terminal: drained` once nothing is pending.

Grant observation separately for the exact project. Exercise every advertised event and inspect
`yoetz observe status`. Only consented accepted `claude_hook` envelopes earn coverage. Raw
transcript/prompt/assistant/path/cwd/tool input/tool output/result/error values are discarded before
storage. The exact successful scoped `start` post-hook is the sole routing exception: it validates
the returned task/session/writer identifiers and frontier to bind the Claude session, while storing
none of the response bytes or prose. Confirm `mapping_present: true`, then drain and require accepted
rows before claiming hook coverage. Pause, resume, revoke, deduplication, restart, and gap behavior
require their own evidence.

### Smart observation selection (issue #687)

Claude's shared hook ingress applies the selector only to the generic tool stream exposed by the
exact ordinary profile `claude-code-ordinary-observation-v1`. The structural profile remains
scoped to the existing Yoetz events; selecting a retention mode does not widen that subscription.
With the ordinary profile, **Focused/standard (512)** is the default. A paired, proven successful
routine `Read`, search, or inventory call may be summarized in Focused mode. Detailed keeps
eligible routine calls as individual records while pressure is healthy. Capacity is independent
of detail: `standard` (512), `larger` (2,048), and `largest` (8,192) can each be selected with
Focused or Detailed. The larger profiles have finite provisional budgets and do not certify a
Claude host cell or improve sustained throughput.

The host outcome remains authoritative. `PostToolUseFailure`, denial, cancellation, interruption,
partial, unknown or conflicting outcome fields, background or incomplete work, edits and side effects,
declared checks/negative verification, and reads protected for an obligation, claim, or finding
remain individual records in both modes. A pre-event keeps its native identity until a post event
proves success; a caller label or tool result field cannot downgrade protection. Claude's
post-only structural profile has no pre-event to pair, so it retains the host's supported
post-only identity contract.

Apply an owner choice from an exact preview:

```text
yoetz observe selection-preview --workspace /exact/project \
  --detail detailed --capacity larger --session-id <claude-session-id> --json
yoetz observe selection-apply --workspace /exact/project \
  --detail detailed --capacity larger --session-id <claude-session-id> \
  --accept --preview-digest <preview-digest> --json
```

This is a temporary session override unless `--persist` is supplied on both preview and apply
without `--session-id`; `--persist` is the explicit workspace choice. An optional RFC3339 UTC
`--expires-at` bounds either setting. `selection-status` reports the selected and effective values;
`selection-revoke` restores the safe fallback at the matching scope. These controls affect future
retention only. They do not rewrite accepted rows, extend Claude's five-second ordinary-event,
ten-second session, or three-second teardown budgets, or change content, privacy, provider, or
network authority. Under pressure, a selected Detailed session can be effectively Focused while
the owner setting remains Detailed.

Use `protect-read` before an upcoming read when a later claim needs its individual identity. The
reference must be an `obl_`, `clm_`, or `fnd_` identifier; at most 32 logical reads are outstanding,
and the protection expires after ten minutes by default (an explicit expiry cannot exceed that
bound). This is a narrowing hint under existing observation consent and grants no content or
disclosure authority. `promote` can only promote an exact native identity while it remains in the
bounded buffer. After delivery it reports `promotion_window_closed` and `not_retained`; rerun the
current read as a new observation if historical bytes are required, and do not use that rerun to
prove the earlier state.

Claude ordinary content capture remains a separate consent arm selected by the exact profile.
Its workspace-wide capture lane allows at most 512 staging/pending tickets and 128 MiB of captured
content, independently of the structural capacity choice. The one-second native content drain
window and the host hook deadlines still apply. A timeout, cancellation, incomplete content group,
or service failure leaves partial/unknown coverage or `content_capture_unavailable` where the
boundary permits; it is never silently converted to a successful routine summary. Capture status
proves configuration only, not accepted bytes, semantic selection, or receipt coverage.

Claude Code 2.1.251 passes an MCP tool's `tool_response` to `PostToolUse` as one bare JSON string
of the structured result (captured live on 2026-09-04 with a probe MCP server that returned both a
text block and `structuredContent`; the text block is dropped). The binder admits that shape next
to a `structuredContent` object and a single-text-block content list, and a fixture pins it. A
scoped successful `start` that still binds nothing records `start_bind_unparsed`,
`start_bind_invalid_ids`, or `start_bind_write_failed` in `hook_diagnostics` (issue #581); a start
the service refused records nothing. This capture proves the `PostToolUse` shape only; 2.1.251 is
not added to the evidenced capability-profile table, whose entries mean the whole native contract
was reviewed. The `PostToolUse` payload carries no `claude_code_version`, so version evidence still
comes from `SessionStart`.

The `SessionStart` context for a mapped session names the task, its frontier, the mapped
`session_id` and `writer_id`, and says to continue the task with `start mode=attach` by that
session id (a bare `task_id` is not a selector the guidance accepts, issue #580). The `resume` and
`compact` status probe connects with `--workspace "${CLAUDE_PROJECT_DIR}"` as its repository
locator, so a live mapping answers `active` with a refreshed frontier; a daemon fence refusal
records `status_workspace_unbound` or `status_workspace_mismatch` and keeps the mapping, and only a
genuinely replaced session records `mapping_stale` (issue #578). Claude Code does not use the
Codex-only `hooks session-start` command, so the issue #659 host-cwd fallback applies to it only
through the shared mapped-session lane: an explicit `${CLAUDE_PROJECT_DIR}` remains authoritative,
an empty or unset value stays the typed `workspace_unresolvable` failure with no ingest and no
probe, and a fence refusal records a companion `locator_source_explicit` row. No live Claude
compaction failure was observed for #659; the shared resolver is covered by unit tests only.

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

Claude Code surfaces MCP initialize `instructions` as server instructions in the model's context.
Whether the auto-mode classifier reads them is not documented, so the policy-route destination
disclosure (issue #479: provider, endpoint profile, and host, or the Codex runtime class, plus the
payload bound, read once at bridge startup) is informational on this host; it is not relied on for
admission, which stays with `permissions.allow`.

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

Fallback endpoint pairing (issue #582) is host-independent: whether the evaluator or a paired
API provider serves a given attempt is a service-side dispatch decision recorded in provenance
(`fallback_from`), with no Claude-Code-specific behaviour, plugin, or route input — the route
ceiling applies to dispatch authority regardless of which endpoint serves.


### Large tasks and semantic failure recovery (#674–#676)

This host uses the shared service status snapshot cache and bounded semantic reference selection.
A reduced reference scope reports `semantic_reference_scope_reduced`; it is not full-task semantic
coverage. `failed/case_capacity_exceeded` and `semantic_case_capacity_exceeded` mean the required
packet could not fit before any provider attempt. Select a smaller claim/obligation scope for a new
check. Shorter prose alone need not fix structural capacity.

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

These examples use the existing MCP operations on the pinned local Claude Code cell. Replace
placeholders with values returned by the current call or `SessionStart` context; do not reconstruct
them from memory, `CLAUDE.md`, or the live store.

- **Read retry.** If a `status`, diagnostics, or `status view=operation` read times out, repeat the
  same read intent with a new read `request_id`. Preserve its view, operation filter, cursor, and
  limit. An unreadable read is not evidence that the task or operation is absent.
- **Ambiguous write.** If a `start` response is lost before session/writer IDs are returned,
  replay the exact original `start` body once with the same request ID. Otherwise read
  `status view=operation` with `filter.operation_request_id` set to the original write ID:
  replay only `absent`; use stored `complete`; follow an exact typed continuation and required
  approval before replaying `pending`. Retain and report pending without a continuation,
  `quarantined`, or unknown. Never invent session/writer IDs or create a task to escape a write.
- **Exact-session attach.** When the `SessionStart` context provides a held `session_id`, use that
  exact value as the `mode=attach` selector. `${CLAUDE_PROJECT_DIR}` is the canonical workspace
  fence supplied by the host context; if the request carries identity refs, include the canonical
  `workspace_ref` + `external_ref` pair together, never `workspace_ref` alone. Use the returned
  successor `session_id` and `writer_id`, read `status`, and continue only from that binding. A
  bare `task_id` is not an attach selector.
- **Same-pair fresh conversation.** With no held session, call `start mode=create_or_attach` using
  the exact canonical `${CLAUDE_PROJECT_DIR}` value and the same stable `external_ref` pair, with no
  `session_id`. A remote URL is not a workspace identity, and a fresh Claude conversation does not
  authorize an implicit second task.
- **Explicit sibling handoff.** Use `start mode=create` only after same-task pair/session recovery
  is exhausted, every earlier write has a known terminal outcome, the Claude binding is healthy and
  authorized, and the user has declared one bounded remaining or repaired verification scope. Keep
  `${CLAUDE_PROJECT_DIR}` as `workspace_ref`, choose a different stable `external_ref` such as
  `<work-item>-recovery-v1`, and let the exact returned `start` result pass through Claude's
  `PostToolUse` binder. Verify the sibling's `mapping_present` and returned task/session/writer,
  then publish fresh plan, evidence, checks, and receipt for that scope. State that the predecessor
  receipt, findings, obligations, evidence IDs, and unresolved status remain separate; the sibling
  cannot make the predecessor look resolved. If no new scope exists, keep the old receipt and stop
  instead of creating another sibling.


## Observation recovery diagnostics

Claude Code uses the shared selected-admission and outbox store. New native input obeys current hard
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

Recovery emits fixed `capture_inventory_*` reason counts in its internal maintenance summary;
these are not ledger receipts or a new hook diagnostic format. Historical local selection losses
still need a separately attributed task/check propagation path when no later envelope is admitted.
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

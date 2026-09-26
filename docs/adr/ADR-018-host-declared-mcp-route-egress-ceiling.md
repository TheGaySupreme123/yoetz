# ADR-018 — Host-declared MCP route egress ceiling

**Status:** Accepted (2026-07-29), acknowledged in
[issue #84](https://github.com/TheGaySupreme123/yoetz/issues/84), amended 2026-08-30 for issue
#404 external-runtime dispatch, and amended 2026-09-26 for the issue #857 host-hold advisory.
**Implemented by:** `src/yoetz/mcp/`, `src/yoetz/application/check.py`,
`src/yoetz/ports/control.py`, `src/yoetz/service/`,
`src/yoetz/adapters/integrations/codex_mcp.py`, `src/yoetz/application/serving_route.py`, and
`src/yoetz/cli/host_denial_advisory.py`.
**Relates to:** ADR-006 (AI-powered review provider profiles), ADR-008 (local service/vault trust
boundary), ADR-009 (data egress and privacy), and ADR-012 (first-run setup wizard).

## Context

The durable privacy policy is the authority for whether an external AI-powered review may occur.
That is necessary, but it is not enough for a host that wants to grant unattended approval to a
particular MCP server process. Such a host needs an inspectable upper bound on what that route can
ask Yoetz to do for the lifetime of the process, independent of later policy widening.

MCP `openWorldHint` communicates expected tool behaviour to a host, but is not authority. Advertising
`check` as open-world while asking the host to auto-approve the process makes the host trust a
runtime policy state it did not declare. Advertising it as closed while the route can still request
AI-powered review would be dishonest.

## Decisions

1. **The MCP process has one immutable route profile and an independent serving identity.**
   `yoetz mcp serve` starts the `policy` profile and `yoetz mcp serve --semantic off` starts the
   `strict` profile. Native host carriers declare `--host codex`, `--host claude`, or
   `--host cursor`; portable and legacy/manual carriers default to `--host generic`, which leaves
   the host unproven. Both flags are parsed before the server accepts stdin and cannot be changed by
   an MCP request, an agent field, environment, provider readiness, or a later privacy-policy
   change. The host declaration identifies the serving carrier only; it grants neither host
   admission nor agent-chat attestation.

2. **Strict is a ceiling, not a privacy policy.** The durable policy continues to authorize or deny
   disclosure. The strict route adds a stronger process-local limit: `check` never requests the
   AI-powered review runtime capability and never invokes an AI-powered evaluator. It does not
   disable local checks, local service IPC, receipts, or the other five operations.

3. **The public six-operation schemas stay host-neutral.** `route_profile` and the serving
   `host_profile` exist only in the private local control envelope between the MCP bridge and the
   service; the host profile is carried only for `check`, while route profile remains available for
   `check` and `status`. Agent-supplied fields remain invalid under the frozen public request
   schema.

4. **A requested review fails honestly under strict.** An AI-powered review request returns
   `semantic_status=blocked_by_policy` and
   `semantic_reason=route_semantic_ceiling`. `semantic_required` therefore returns an incomplete
   result. The same reason and explicit gap are retained in the result and receipt; no local-check
   outcome is promoted to AI-powered review coverage.

5. **Descriptor sets are frozen per profile.** The policy profile advertises
   `check.openWorldHint=true`. The strict profile advertises `false` and says that the route will not
   request external AI-powered review. Both exact descriptor sets (six workflow tools plus
   read-only `read_guidance`) and their set digests are conformance-tested. Annotations remain
   untrusted hints; enforcement is owned by the application route constraint.

6. **Initialize and versions status disclose the active profile.** Initialize instructions name
   `policy` or `strict` and state the corresponding bounded promise. MCP-originated
   `status(view=versions)` includes the same route profile. On the policy route the instructions
   also name the configured AI-powered review destination and payload bound, read once at bridge
   startup (destination-disclosure amendment below, issue #479).

7. **Registration binds the exact command.** A host registration preview includes the exact argv,
   route profile, and digest. Zero-egress setup registers Codex with
   `yoetz mcp serve --host codex --semantic off`; an installation whose configured posture permits
   AI-powered review registers `yoetz mcp serve --host codex`. A Yoetz-owned registration with the
   wrong profile requires a fresh digest-bound re-registration. A foreign same-name entry is still
   preserved.

## Consequences

A host can inspect one process command and safely treat the strict route as incapable of external
AI-powered review dispatch through Yoetz, even if a local human later widens durable policy. The
claim is deliberately narrower than “no network”: the MCP bridge still uses approved local IPC, and
processes outside Yoetz remain outside this boundary.

There are now two reviewed descriptor digests and two owned Codex registration commands. Changing
either command or either descriptor set is a public-contract change and must update this ADR,
documentation, schemas where applicable, fixtures, and conformance evidence together.

**Amendment (ADR-023, 2026-08-21, issue #149): a plugin-managed `mcp.json` is a third generated
route surface under the same ceiling.** When a portable plugin artifact carries `mcp.json`
(`plugin_managed` ownership only), that file is generated exclusively from the
`PortablePluginPlan`'s bound route: the exact argv and `strict|policy` profile are chosen before
approval and bound into the preview and artifact digests, and runtime configuration, environment,
agent input, or later privacy widening cannot change the route — the same immutability decision 1
gives the process flag. An `external_registration` artifact omits `mcp.json` entirely; the
existing registration commands above remain authoritative. No second native or global registration
may own the `yoetz` server name while a plugin-managed declaration exists: dual, foreign, and
ambiguous ownership are explicit `McpOwnershipState` values that are reported, never overwritten
or silently chosen between. Changing the generated `mcp.json` bytes for a bound route is a
public-contract change under the same update rule as the two registration commands.

**Amended 2026-08-21 — the route profile is explicit registration input (issue #389).** Live
testing showed non-interactive `setup run --accept` silently re-registering an existing
yoetz-owned *policy* route as *strict*, because the registration-time route was derived from
structural configuration (falling back to strict on any load failure) with no route input surface.
That violated this ADR's premise that the registered argv is a deliberately chosen, host-inspectable
ceiling: no derivation — and especially no derivation-on-exception in a degraded environment — may
rewrite a previously chosen route in either direction. `setup run` and
`integrate codex mcp preview|install` now accept `--route-profile strict|policy`; without it an
existing yoetz-owned registration keeps its observed profile, a fresh registration falls back to
strict (wizard) or the configuration derivation (`integrate`), and any transition of an existing
owned route is surfaced (`route_profile_before` → `route_profile`) before the ordinary
digest-bound re-registration.

**Issue #151 implementation detail.** The portable projection emits one closed stdio server named
`yoetz`. Its executable token is exactly `yoetz`; policy args are exactly `mcp serve`, and strict
args are exactly `mcp serve --semantic off`. Portable routes retain the generic unknown host
identity and emit no `env`, headers, credential references, or
shell command. The pinned Agent Plugins schema is validated offline. Invalid top-level MCP config
disables only MCP; an invalid, unsupported, or failing entry skips only that server, so the
independent Yoetz skill remains loadable. Preview binds the full `mcp.json` bytes through
inventory/artifact digests and also binds the observed `McpOwnershipState`; changed ownership
makes apply stale or conflicting before mutation.

**Amended 2026-08-30 — host admission is the fourth route-bound surface (issue #467).** Every
supported host now ships a model-based automatic tool-call reviewer (Claude Code auto mode, Codex
`approvals_reviewer = "auto_review"`, Cursor Auto-review), and each refuses the policy-route
`check` on the same criterion — data to a destination the user did not name — because the owner's
`yoetz --privacy` authorization is invisible to it. Decision 5 stands: annotations stay honest and
`openWorldHint` is never softened to slip past a reviewer. The mirror image of the strict ceiling
is added instead: the owner's trusted decision, never the agent and never a self-approving hook,
tells the host to admit the call. `yoetz integrate <host> admission preview|grant|revoke|status`
writes each host's *own* project-scoped admission entry for exactly `check` — Claude Code
`permissions.allow` (or `ask`) in `.claude/settings.local.json`, using
`mcp__yoetz__check` for an external registration or
`mcp__plugin_yoetz_yoetz__check` for the plugin-owned route; Codex
`[mcp_servers.yoetz.tools.check] approval_mode = "approve"` (or the `plugins."yoetz@yoetz"`
form) in `.codex/config.toml`, Cursor `mcpAllowlist` in `.cursor/permissions.json` plus
`Mcp(...)` in `.cursor/cli.json` — only through a previewed, digest-bound step that binds the
exact file bytes, only on an observed `policy` route, only when the repository grant permits
external review, and never over a foreign (wider, conflicting, or non-exact) entry; an unreadable
host file is `unknown`, never `absent`. Apply rechecks the exact preimage immediately before each
atomic file mutation and verifies the resulting admission state. Ordinary host files provide no
compare-and-swap primitive against a non-cooperating same-UID writer in the final syscall window,
so the preview warns `host_config_not_compare_and_swap` and the owner must keep host configuration
writers quiescent during apply. Every reverse transition — grant revoke in the privacy
ceremony, strict re-registration, unregistration, and host uninstall for the named project —
removes exactly the entry Yoetz wrote, and `yoetz provider status` reports a leftover entry as
`host_admission_drift` beside `host_admission` per host (`absent|present|partial|foreign|unknown`).
Admission is host tool-call authorization: it proves no dispatch, widens no policy, and bypasses
no privacy, disclosure, credential, or human-review gate. Yoetz records a Claude Code
`PermissionDenied` on a scoped `check` as a payload-free `host_auto_review_denied` diagnostic;
Codex and Cursor expose no typed denial and that gap is documented. Rejected: shipping a
`PermissionRequest` / `beforeMCPExecution` hook that approves Yoetz's own tool (inverts the
authority this ADR keeps with the host), widening the egress admission entry to the other tools
(their local or read-only effects do not invoke a provider, but a host may still review them),
customizing a host's reviewer policy on the user's behalf, and relaying "the user authorized this"
through the agent (the prompt-injection shape #187 forbids).

## Alternatives considered

**Infer the route from current policy for each call.** Rejected: the host would be approving a
moving target, and later policy widening could silently expand a process it had auto-approved.

**Accept `route_profile` in the public `check` request.** Rejected: an agent-controlled field cannot
be the authority for the host's process ceiling, and adding it would weaken the surface boundary.

**Set `openWorldHint=false` without enforcement.** Rejected: annotations are advisory metadata, not
an enforcement mechanism.

**Disable AI-powered review globally when strict is registered.** Rejected: a route-local trust
choice must not silently tighten other CLI, UI, or MCP processes.

## External-runtime amendment (2026-08-30, issue #404)

The route ceiling applies to dispatch authority, not transport shape. A strict MCP process cannot
request either an HTTP `yoetz_vault_api_credential` attempt or a child-process
`external_runtime_oauth` attempt. It reaches neither provider factory, credential/runtime
authority, privacy authorization, nor Codex child launch, and reports the existing
`blocked_by_policy/route_semantic_ceiling` pair. A policy route merely permits the ordinary privacy
decision path; it does not imply ChatGPT login, model entitlement, repository approval, or a live
AI-powered review attempt.

## Destination-disclosure amendment (2026-09-06, issue #479)

The #467 amendment made the owner's host admission the lever that admits the policy-route
`check`; it left the initialize `instructions` saying only that external AI-powered review
"follows the configured policy". A reviewer that reads descriptions — Codex copies the
instructions into every tool description — therefore scored the call from no named destination,
and a repository without admission had nothing better to show it. Decision 6 is extended: on the
policy route the bridge appends one bounded passage, rendered by `mcp/semantic_destination.py`
from the configuration it reads once at startup, that names the destination the route would
dispatch to and the payload bound.

What the passage may contain is closed. The endpoint profile id and provider id are echoed only
when they are bundled catalog tokens (`BUNDLED_ENDPOINT_HOSTS`, `DISCLOSABLE_PROVIDER_IDS`); the
host is the catalog's host for that endpoint profile, which a unit test locks to the adapter that
dials it, or — for the owner-declared Responses profile — the hostname and port that already
passed the HTTPS-origin validator, never the origin string itself. The Codex subscription runtime
is named as a runtime class under its own ChatGPT login, and the passage states that Yoetz does not
name that runtime's upstream host. A provider id outside the allowlist renders as *unlisted*; an
endpoint profile outside the catalog renders as an *unknown* host; absent, unreadable, or invalid
configuration renders as *unknown*, never as a guess; `verification.semantic = "disabled"`, a
strict-local or test-fake profile, and a local-model-only binding render as *none* with the
reason. A `[semantic_fallback]` pairing discloses the fallback endpoint beside the primary, because
a reviewer told that only the primary can receive data would be misled. No secret, filesystem path,
URL, query string, model name, repository handle, or free-form configuration prose can reach the
text; the value is typed (`SemanticDestinationDisclosure`) so no caller can pass a string.

Staleness is handled by disclosure, not detection. The bridge process has one immutable route
(decision 1) and reads configuration once, so the passage is stamped "read once at bridge
startup" and a later route change is reflected only when the host restarts the bridge. The live
authority for what a given check did remains that check's recorded `semantic_status`, provider
attempt, and receipt. Strict instructions are byte-identical to before this amendment whatever the
configuration says, and annotations are unchanged (decision 5 stands).

The passage is disclosure, not authority. It does not admit the call — Codex's guardian policy
still requires trusted user content, Cursor's classifier inputs are undocumented, and Claude Code
auto mode separates permissions from classifier context — and it does not widen privacy policy,
prove a dispatch, or replace the privacy ceremony that authorized the destination. Because the
packaged `agent-instructions.md` already sat within a few hundred bytes of the #300 instructions
budget, `SERVER_INSTRUCTIONS_BUDGET` and `ADVERTISED_SURFACE_BUDGET` now carry two numbers each:
the unchanged bound on the packaged text, and that bound plus the disclosure ceiling
(`MAX_DISCLOSURE_ENCODED_BYTES`, charged once per advertised tool in the aggregate), which the
longest admissible passage is tested against.

The startup disclosure is a configuration snapshot, not a guarantee about the live service.
Absent or invalid configuration remains unknown; a policy-route check may still reach an
external reviewer whose destination the bridge could not determine. Even a valid snapshot
with no external binding can differ from the independently running service configuration.

## Host-hold advisory amendment (2026-09-26, issue #857)

Host admission (the #467 amendment) is the durable lever, but a repository without it, or after
admission drift, still sees every policy-route `check` held by the host's automatic reviewer. The
agent then sees only the host's fixed refusal and nothing first-hand saying the owner already
authorized the review, and in practice downgrades to deterministic-only or abandons review. This
amendment lets Yoetz state its own recorded fact at that moment. It is information, never
authorization.

**Claude Code `PermissionDenied`.** The scoped hook (matched to exactly the external and
plugin-owned `check` names) keeps its payload-free `host_auto_review_denied` /
`host_permission_rule_denied` row and now also emits one closed advisory. The hook reads three
facts first-hand within its five-second budget: the repository grant as the running service reports
it through the workspace-bound connection (`grant_state: granted` with the `llm_inference` channel
enabled), the route the host's bridge recorded it is serving, and the host's own admission file.
Three closed texts exist:

- *Grant confirmed*, served on `policy`, source the auto-mode classifier. The advisory states the
  owner's authorization and that the host, not Yoetz, held the call. It emits
  `hookSpecificOutput.retry: true` once per `(session_id, tool_use_id)` and a user-visible
  `systemMessage` naming the durable admission command. A second hold of the same call gets the
  pause text with no `retry`: present the exact call for the user's manual approval.
- *Grant not confirmed* for any reason (service unavailable, vault locked, grant absent or not
  permitting, grant unverifiable, route unobserved, route strict). A closed reason token is
  appended. There is no `retry`, and the agent is told to ask before any retry.
- *Owner's own rule* (`source` `permission_rule` / `hook`, or reason `denied_by_rule`). There is
  no `retry`, and the agent is told to ask the user.

`reason: no_verdict`, a call without a bounded session and tool-use identity, or a failure to
record the offer never produces `retry`. The offer marker stores only domain-separated SHA-256
digests of the host identifiers, bounded to the most recent 256 offers. The advisory, the retry,
and any host approval that follows are not Yoetz privacy, disclosure, credential, or repository
authority. The retried call goes back through the host's own permission flow. No hook emits a
`PermissionRequest` / `PreToolUse` allow decision, writes an admission entry, or edits host
configuration. The rejected alternatives of the #467 amendment stand: this is Yoetz stating its
own first-hand record only when it read that record, not the agent relaying "the user authorized
this".

**Serving-route record.** A hook has no serving route of its own, and #537 forbids a host
subprocess inside the hook budget. The bridge therefore records, at startup, the closed pair
`(host_profile, route_profile)` for an explicit `claude`, `codex`, or `cursor` host identity in
`integrations/serving-routes.json` under the state directory. A generic or legacy bare `mcp serve`
records nothing, so its hook reads the route as unobserved and never offers a retry. The record is
a snapshot of the last bridge start for that host on this machine, not a live guarantee. The next
bridge start overwrites it, and uninstall removes it with the state directory.

**All hosts, `SessionStart`.** When the host's recorded serving route is `policy`, its own
admission file reads exactly `absent`, and the service confirms the grant permits external review,
the `SessionStart` context gains one bounded line naming `yoetz integrate <host> admission grant`.
Any unread fact keeps it silent. This is the only proactive surface on Codex and Cursor, which
expose no typed post-denial event. On those hosts the agent-facing rule in guidance and skills
remains the whole hold response.

**Diagnostics.** `host_denial_retry_offered`, `host_denial_retry_exhausted`, and
`host_denial_grant_unconfirmed` join the closed hook-diagnostic vocabulary on the
`PermissionDenied` event beside the existing hold row.

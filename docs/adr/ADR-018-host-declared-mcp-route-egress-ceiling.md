# ADR-018 — Host-declared MCP route egress ceiling

**Status:** Accepted (2026-07-29), acknowledged in
[issue #84](https://github.com/TheGaySupreme123/yoetz/issues/84), and amended 2026-08-30 for issue
#404 external-runtime dispatch.
**Implemented by:** `src/yoetz/mcp/`, `src/yoetz/application/check.py`,
`src/yoetz/ports/control.py`, `src/yoetz/service/`, and
`src/yoetz/adapters/integrations/codex_mcp.py`.
**Relates to:** ADR-006 (semantic provider profile), ADR-008 (local service/vault trust
boundary), ADR-009 (data egress and privacy), and ADR-012 (first-run setup wizard).

## Context

The durable privacy policy is the authority for whether an external semantic review may occur.
That is necessary, but it is not enough for a host that wants to grant unattended approval to a
particular MCP server process. Such a host needs an inspectable upper bound on what that route can
ask Yoetz to do for the lifetime of the process, independent of later policy widening.

MCP `openWorldHint` communicates expected tool behaviour to a host, but is not authority. Advertising
`check` as open-world while asking the host to auto-approve the process makes the host trust a
runtime policy state it did not declare. Advertising it as closed while the route can still request
semantic review would be dishonest.

## Decisions

1. **The MCP process has one immutable route profile.** `yoetz mcp serve` starts the `policy`
   profile. `yoetz mcp serve --semantic off` starts the `strict` profile. The flag is parsed before
   the server accepts stdin and cannot be changed by an MCP request, an agent field, environment,
   provider readiness, or a later privacy-policy change.

2. **Strict is a ceiling, not a privacy policy.** The durable policy continues to authorize or deny
   disclosure. The strict route adds a stronger process-local limit: `check` never requests the
   semantic runtime capability and never invokes a semantic evaluator. It does not disable
   deterministic checks, local service IPC, receipts, or the other five operations.

3. **The public six-operation schemas stay host-neutral.** `route_profile` exists only in the
   private local control envelope between the MCP bridge and the service, and only for `check` and
   `status`. An agent-supplied field remains invalid under the frozen public request schema.

4. **A requested review fails honestly under strict.** A semantic request returns
   `semantic_status=blocked_by_policy` and
   `semantic_reason=route_semantic_ceiling`. `semantic_required` therefore returns an incomplete
   result. The same reason and explicit gap are retained in the result and receipt; no deterministic
   outcome is promoted to semantic coverage.

5. **Descriptor sets are frozen per profile.** The policy profile advertises
   `check.openWorldHint=true`. The strict profile advertises `false` and says that the route will not
   request external semantic review. Both exact descriptor sets (six workflow tools plus
   read-only `read_guidance`) and their set digests are conformance-tested. Annotations remain
   untrusted hints; enforcement is owned by the application route constraint.

6. **Initialize and versions status disclose the active profile.** Initialize instructions name
   `policy` or `strict` and state the corresponding bounded promise. MCP-originated
   `status(view=versions)` includes the same route profile.

7. **Registration binds the exact command.** A host registration preview includes the exact argv,
   route profile, and digest. Zero-egress setup registers Codex with
   `yoetz mcp serve --semantic off`; an installation whose configured posture permits semantic
   review registers the policy command. A Yoetz-owned registration with the wrong profile requires
   a fresh digest-bound re-registration. A foreign same-name entry is still preserved.

## Consequences

A host can inspect one process command and safely treat the strict route as incapable of external
semantic dispatch through Yoetz, even if a local human later widens durable policy. The claim is
deliberately narrower than “no network”: the MCP bridge still uses approved local IPC, and processes
outside Yoetz remain outside this boundary.

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
args are exactly `mcp serve --semantic off`. It emits no `env`, headers, credential references, or
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
authority this ADR keeps with the host), widening admission to the other tools (they need none),
customizing a host's reviewer policy on the user's behalf, and relaying "the user authorized this"
through the agent (the prompt-injection shape #187 forbids).

## Alternatives considered

**Infer the route from current policy for each call.** Rejected: the host would be approving a
moving target, and later policy widening could silently expand a process it had auto-approved.

**Accept `route_profile` in the public `check` request.** Rejected: an agent-controlled field cannot
be the authority for the host's process ceiling, and adding it would weaken the surface boundary.

**Set `openWorldHint=false` without enforcement.** Rejected: annotations are advisory metadata, not
an enforcement mechanism.

**Disable semantic review globally when strict is registered.** Rejected: a route-local trust
choice must not silently tighten other CLI, UI, or MCP processes.

## External-runtime amendment (2026-08-30, issue #404)

The route ceiling applies to dispatch authority, not transport shape. A strict MCP process cannot
request either an HTTP `yoetz_vault_api_credential` attempt or a child-process
`external_runtime_oauth` attempt. It reaches neither provider factory, credential/runtime
authority, privacy authorization, nor Codex child launch, and reports the existing
`blocked_by_policy/route_semantic_ceiling` pair. A policy route merely permits the ordinary privacy
decision path; it does not imply ChatGPT login, model entitlement, repository approval, or a live
semantic attempt.

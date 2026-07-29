# ADR-018 — Host-declared MCP route egress ceiling

**Status:** Accepted (2026-07-29), acknowledged in
[issue #84](https://github.com/TheGaySupreme123/yoetz/issues/84).
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
   request external semantic review. Both exact six-tool descriptor sets and their set digests are
   conformance-tested. Annotations remain untrusted hints; enforcement is owned by the application
   route constraint.

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

## Alternatives considered

**Infer the route from current policy for each call.** Rejected: the host would be approving a
moving target, and later policy widening could silently expand a process it had auto-approved.

**Accept `route_profile` in the public `check` request.** Rejected: an agent-controlled field cannot
be the authority for the host's process ceiling, and adding it would weaken the surface boundary.

**Set `openWorldHint=false` without enforcement.** Rejected: annotations are advisory metadata, not
an enforcement mechanism.

**Disable semantic review globally when strict is registered.** Rejected: a route-local trust
choice must not silently tighten other CLI, UI, or MCP processes.

# Yoetz architecture

How the system is put together and why. This page describes shape and ownership; the code under
`src/yoetz/` is the executable truth, and the ADRs under [`docs/adr/`](adr/) are the decisions that
bind it. Shared names — ID prefixes, error codes, event families, coverage dimensions, port
signatures — live in [`docs/INTERFACES.md`](INTERFACES.md).

## The one-sentence version

A persistent local service owns the keys, the decrypted state, the writers, the privacy authority,
and all outbound access; the CLI, the MCP bridge, and any future UI are clients that ask it to do
things.

## Trust topology

```
  agent (via MCP)   human (via CLI)   human (terminal UI)
         \                   |                  /
          \                  |                 /
           +--------- control protocol --------+
                              |
                    +---------v----------+
                    |   trusted local    |   owns: vault keys, decrypted state,
                    |      service       |          storage writers, privacy policy,
                    +---------+----------+          outbound dispatch
                              |
        +---------------------+---------------------+
        |                     |                     |
   SQLite bundle        object store           provider egress
   (structural)         (encrypted)            (policy-gated)
```

The boundary is the point. A client process never holds an encryption key or a decrypted bundle
handle, and never talks to a provider directly. That is what makes "the agent cannot quietly
exfiltrate your repository" a structural property rather than a promise
([ADR-008](adr/ADR-008-local-service-vault-trust-boundary.md),
[ADR-009](adr/ADR-009-data-egress-privacy.md)).

Privacy authority has two durable levels: the machine policy is an installation ceiling, while
external LLM work also requires an exact row for the installation-keyed commitment of the trusted
session's canonical repository. The service derives that identity from actual/configured working
directory, resolves Git's common root (or a resolved non-Git directory), and discards the raw path.
Branches and linked worktrees share authority; independent clones do not. The public
`workspace_ref` used to attach a task is a different, model-controlled identity and cannot select
privacy authority.

Secret material never travels over an ordinary CLI argument, environment variable, config file,
log, trace, transcript, or LLM context. Credentials are provisioned through a confidential terminal
ceremony ([ADR-015](adr/ADR-015-elevated-bootstrap-consent.md),
[ADR-016](adr/ADR-016-human-review-non-default-actions.md)).

The terminal interface sits on the human-CLI leg and is bound by the same rule. It has no
credential path of its own: when a secret is needed it suspends itself and hands the controlling
terminal to that same ceremony, so no secret byte enters the interface's widget state, transcript,
or any snapshot of it ([ADR-017](adr/ADR-017-full-screen-terminal-interface.md) decision 7).

## Module map

| Package | Role |
|---|---|
| `protocol/` | Canonical wire form: IDs, request/result models, canonical JSON, digests. Pure. |
| `domain/` | Values and events — the vocabulary of what happened. Pure. |
| `kernel/` | Deterministic truth: reducers, projections, the check engine, ranking, proof-based finding resolution, receipt building, and the versioned policy packs under `kernel/policies/`. Pure — no IO, no clock, no network. |
| `ports/` | The interfaces the application depends on: ledger, objects, keys, runtime, semantic, privacy, importer, integrations, subject state. |
| `adapters/` | Concrete implementations of those ports: `sqlite/`, `objects/`, `keys/`, `memory/` (the in-memory reference used for conformance parity), `providers/`, `privacy/`, `importers/`, `integrations/`, `control/`, plus `mcp_stdio.py` and `git_subject_state.py`. |
| `application/` | Use cases. One module per public operation (`start`, `publish_work`, `check`, `respond`, `status`, `receipt`), plus egress, privacy policy, maintenance, import review, harness integration, and the observation coordinator. |
| `service/` | The persistent trusted process: lifecycle, vault, unlock, control protocol, composition. |
| `cli/`, `mcp/` | Client surfaces. Thin — they translate, they do not decide. |
| `tui/` | The full-screen terminal interface. Presentation only. `runtime.py` is the sole bridge to application services and originates no decision; `render.py` is pure text with no rendering-framework import, so safety-relevant wording is snapshot-tested; `widgets/` holds no security logic. |
| `config/`, `observability/`, `version.py` | TOML settings, privacy-safe logging, resource manifest and identity. |

`memory/` existing alongside `sqlite/` is deliberate: the conformance suite runs the same scenarios
against both, so "the durable adapter behaves like the reference" is a test result rather than an
assumption.

`tui/` sitting beside `cli/` rather than inside it is also deliberate. It is a second presentation
of the same operations, not a second authority over them: every gate — preview digests, foreign
MCP entries, privacy widening, vault state, provider readiness — is enforced by the owning service
and merely transcribed by the interface. The split makes that reviewable, because anything in
`tui/` that reached past `runtime.py` would be visible as an import.

## The six operations

`start`, `publish_work`, `check`, `respond`, `status`, `receipt` — identical request and result
contracts on the CLI and over MCP ([ADR-002](adr/ADR-002-canonical-protocol.md),
[ADR-010](adr/ADR-010-harness-integration-port.md)). Everything else — import/review,
backup/restore/migrate, integration, version, service, MCP serving — is a bounded support surface,
not a seventh operation.

Flow for a typical task:

1. **`start`** opens a task and issues a session and a writer identity.
2. **`publish_work`** records bounded, participant-published facts: plan, obligations, claims,
   actions, results, evidence. The participant publishes; Yoetz does not observe the workspace.
3. **`check`** runs the deterministic policy packs over the recorded state, producing findings with
   an exact coverage vector. If semantic review is configured and requested, an advisory
   provenance-labeled pass runs inside the privacy policy and is deterministically fenced.
4. **`respond`** answers a finding: act, supply evidence, revise the claim, dispute, or state an
   unresolved limitation. A response never erases a finding.
5. **`receipt`** projects the honest summary: what was checked, at what coverage, what is still
   open. Available as `json`, `markdown`, or `text`.

## Determinism and honesty rules

These are enforced in code and locked by tests; they are the reason the system is worth trusting.

- **Coverage-bounded language.** "No issue detected at coverage X" is never rendered as "verified".
- **Deterministic results depend only on canonical recorded inputs** plus the versioned policy and
  engine identity. Same inputs, same version, same findings.
- **Semantic output is advisory**, provenance-labeled, and fenced. `semantic_required` never erases
  a completed deterministic result: unavailability returns that result as `incomplete_check` with an
  exact gap, not a failure.
- **Nothing user-controlled** — payloads, titles, paths, prompts, model output — reaches SQLite
  structural tables, logs, errors, or MCP text summaries.
- **Every retryable write has an idempotency identity.** A timeout never proves failure.
- **Every network channel is independently authorized.** No profile overrides the never-send set,
  and only a reauthenticated local human can loosen effective policy.
- **Machine capability is not repository consent.** External LLM admission requires the exact
  repository grant before provider construction or credential minting; a first grant may atomically
  combine a machine-ceiling widening with repository-row creation.

## Storage

One SQLite bundle per task holds structural rows; content-bearing material lives in the encrypted
object store and is referenced by digest. Migrations under `migrations/` are append-only and CI
enforces that ([ADR-003](adr/ADR-003-storage-sqlite-durability.md)). Recovery paths — backup,
restore, migrate, quarantine — are documented in [`docs/runbooks/`](runbooks/).

Catalog migration preserves accepted machine-policy bytes. It may consume only bounded
pre-upgrade route or one first-repository entitlement to clone that authority into a narrower child
row without reapproval; later repositories inherit nothing. Schema decoders for older control
shapes remain readable but cannot bypass the repository/authority-digest gate.

## Harness integration

Yoetz works with any MCP host with no integration, no installed skill, and no configuration: the
host gets the six operations, read-only `read_guidance`, a short always-delivered instruction set,
and the same guidance documents every harness ships, fetchable on demand.

Codex is the first harness with a first-party integration because its skill surface delivers that
guidance natively. The guidance itself is harness-neutral, owned once under [`guidance/`](../guidance/),
and shipped byte-identically everywhere. Integration is a port with Codex as its first adapter
([ADR-010](adr/ADR-010-harness-integration-port.md)) — a fork can make Yoetz first-party on another
harness by adding an adapter and a profile, without touching the core.

Integration buys ergonomics, never a stronger claim. An agent publishing over MCP earns the weakest
honest coverage, and the coverage vector says so exactly.

The accepted packaging design extends this reach without moving any authority
([ADR-023](adr/ADR-023-portable-plugin-carrier-host-activation.md); its skills-only artifact slice
is implemented by issue #150 and later capabilities remain phased under #148). One neutral
`PortablePluginPlan` projects into either a portable
Agent Plugins 1.0.0 artifact, for hosts that consume the universal standard in their own client
plugin root, or a generated host-native projection in a distinct documented root for hosts that do
not. The artifact is a carrier, never an authority: installation, activation, MCP ownership, and
observation stay separately owned sibling capabilities (`PluginArtifactPort`,
`HostActivationPort`, the existing `HarnessMcpPort` and `ObservationPort`), and format
compatibility with the standard earns no support claim, activation claim, or coverage.

`src/yoetz/ports/plugin_artifacts.py` owns the neutral plan and lifecycle types.
`src/yoetz/adapters/integrations/portable_plugin.py` owns the Agent Plugins 1.0.0 projection,
offline validation, optional exclusive route-bound `mcp.json`, Codex-root preview/status, and
failure-atomic whole-directory migration. It
reads only packaged `skills/portable/`, `guidance/`, and vendored `support/agent-plugins/` bytes.
The existing `codex_plugin.py` remains the native fallback and is not a source for the portable
manifest.

Cursor is the second first-party harness and remains local-only. The adapter at
`adapters/integrations/cursor_integration.py` owns exact IDE/CLI/SDK identity capture, the
Agent Plugins and Cursor-native projections, explicit user-scope lifecycle, MCP source precedence,
live classified MCP process identity for activation mismatch (`cursor_mcp_runtime.py`),
and the pinned `sdk.v1` TypeScript/Python profiles. The pinned macOS lifecycle consumes one exact
`plugin_artifact_apply` pending only after the scoped `macos_artifact_presence.py` adapter obtains
a fresh Apple LocalAuthentication device-owner result; this does not populate the service-wide
presence cell. Cursor-native hooks enter through
`hooks cursor-observe`; that boundary retains only allowlisted structural scalars and a one-way
changed-path digest. It drops prompts, reasoning, response text, file paths and contents, edits,
MCP arguments/results, transcripts, command output, and email before observation storage. The
adapter never reads or mutates Cursor caches. Cursor Cloud is not a registered local capability.

Claude Code is the third first-party harness, but it is a native dual target rather than an Agent
Plugins client. `adapters/integrations/claude_code_integration.py` generates a strict private local
marketplace and Claude-native plugin from `PortablePluginPlan`, then delegates project installation,
update, enablement, disablement, and qualified removal to the exact Claude executable. Status reads
Claude's project settings/list result and exact cache bytes while keeping enablement distinct from
loaded-session activation. It observes local/project/user/plugin/connector MCP sources without
turning Claude's precedence winner into a Yoetz ownership claim.

Claude-native hooks enter through `hooks claude-observe`. Only the exact scoped Yoetz MCP tool names
and bounded structural lifecycle facts survive; raw Claude prompt/transcript/path/tool/result/error
content is discarded before storage. The `claude_hook` source is carried on local-control schema
`2.2.0`. The initial capability fixture covers only local CLI `2.1.241`, macOS arm64, project scope,
and private marketplace install. Desktop, remote, web/cloud, synced, managed/user/local scope,
Agent SDK, loaded-session/model-use, semantic, privacy-receipt, and workflow-receipt cells require
their own evidence.

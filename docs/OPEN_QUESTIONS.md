# Yoetz v0.1 — decision ledger and implementation-freeze gates

**ADRs:** ADR-001 through ADR-016 | **Related:** [`docs/INTERFACES.md`](INTERFACES.md),
[`docs/adr/`](adr/), release-evidence generation

## Purpose

Keep unresolved choices visible in one place instead of scattering founder questions, empirical
calibration, and deferred features across the repository. An ADR or a code comment may explain a
local uncertainty; this file is the one queue used to decide whether a release claim may ship.

## How to read an item

Every item has a stable ID, class (`founder`, `empirical`, `independent-review`, or `deferred`), a
working default, an owner, and a freeze condition. An item is removed only by recording the answer
in its owning ADR and moving a short result into the resolved-decisions section below.

## Behavior

Before implementation starts, all `founder` items must be accepted or deliberately amended. Before
the affected release claim ships, all `empirical` and `independent-review` items must have dated,
artifact-bound evidence. `deferred` items do not block v0.1 and may not leak into v0.1 help,
schemas, capability claims, or implicit adapter behavior.

## Errors and edge cases

- Silence is not acceptance: a working default remains visibly provisional until its freeze
  condition is met.
- A library/tool version is never settled by prose alone; the exact release lock and executable
  capability evidence are authoritative.
- An empirical threshold cannot quietly change protocol identity or honest wording. Such a change
  requires an ADR amendment.
- A deferred feature cannot be implemented opportunistically while adjacent code is being built.

## Invariants

1. Ignored architecture/strategy inputs are never required to interpret an item or its answer.
2. Founder questions describe product/legal choices, not measurements engineers can answer.
3. Release claims remain narrower than the evidence produced by the applicable gate.
4. Closing an item updates every affected ADR, code path, test, and fixture in the same review.

## Enforcement

`scripts/scan_public_boundary.py` rejects private drafting dependencies;
`tests/conformance/claims/` binds every public claim in `docs/public-claims.json` to real evidence
and holds each claim at its honest `release_status`; release-evidence generation refuses open
applicable empirical/review gates.

## Open items

### Phase 0 implementation reconciliation — 2026-07-17

The pre-code consistency pass rechecked the private P0–P4 review catalogue against the current
public authorities and closed every still-live implementation ambiguity required by the next
materialization boundary. Later-wave byte-identity gates remain explicit below and must close
before their owning modules are coded:

| Review seam | Frozen resolution | Public authorities updated |
|---|---|---|
| P0-1 finding kind/origin and fixture ownership | `FindingKind` and `FindingOrigin` are independent; the exhaustive fourteen-kind trait table fixes priority/actionability, and the reviewed 48-file corpus covers every kind without inventing a `fixtures/policies/` family. | `INTERFACES.md`, finding/policy/fixture owner specs and conformance specs |
| P0-2 verdict versus receipt conclusion | Checks retain four `CheckVerdict` values; receipts retain three `ReceiptConclusion` values, with one exact projection-derived correspondence and conservative precedence. | `INTERFACES.md`, ranking, receipt, event, schema, fixture and reducer specs |
| P0-3 shared-registry drift | `INTERFACES.md` owns shared names and semantics. The pass reconciled ranking context/coverage, canonical depth, adapter-only frame caps, schema kind/role maps, control/privacy methods, operation recovery values, secret purposes and human-authority values. | `INTERFACES.md` and every affected owning spec |
| P0-4 unsupported non-LLM channel fork | A proposal to enable an unavailable v0.1 non-LLM channel is rejected immediately as `channel_unavailable`, creates no pending consent or I/O, and a crafted/imported enabled row remains fenced while producing a structural no-dispatch receipt. | ADR-009, privacy policy/domain/fixture and gateway specs |
| P1–P4 implementation gaps | Receipt reads, recorded coverage, memory/SQLite parity, catalog DDL ownership, keyed scope commitments, service-security ownership, idle-relock reachability, exact TLS wording, recovery contracts, CLI/error mappings and dangling names were reconciled. | Affected ADRs, owning specs, schemas, fixtures and test specs |
| E-001 implementation lock | The build lock is frozen to the exact 2026-07-17 versions in ADR-007 and repository owner specs; this does not manufacture platform/provider support evidence. | ADR-007, repository owner specs and E-001 below |
| Wave B implementation lock (2026-07-18) | B0 now has one closed 127-reason registry, implementable Pydantic 2.13 root/presence semantics, deeply immutable package-only schema loading, and a 52-route schema bundle whose gates resolve entirely from local resources. | `INTERFACES.md`, protocol/model/schema owner specs, coverage schema and manifests |
| Wave B replay/policy lock (2026-07-18) | Redaction-safe full replay uses one nonplaintext durable projection locator per accepted event; projection records/gaps and contradiction lifecycles are exact. Deterministic bases have explicit status/outbound mappers, complete subject cardinality/order/templates, and fixture-aligned coverage/frontiers. | Event/projection/reducer/migration, deterministic/policy/semantic specs, REP/ADV fixtures and tests |
| Wave B port/status lock (2026-07-18) | Frozen checks always carry the current lease, replay has a distinct internal commit result, dependency staleness has one terminal conflict outcome, row queries own typed positions, and candidate status is the deterministic whole-case exception. | Ledger/memory/SQLite/status/check specs, `INTERFACES.md`, conformance/property/integration tests |
| Wave B1 domain lock (2026-07-18) | Domain materialization uses exact canonical strings/timestamps/sequences, closed local coverage/frontier codecs, lossless receipt refs/sections, selected-attempt semantic provenance, lossless lifecycle identity bounds, exact receipt-object mirrors, and an operation/channel event-family admission matrix. | Value/clock/coverage/model/event/finding/receipt/application owners, schema owners and closest tests |
| Wave B2 kernel lock (2026-07-19) | Deterministic ranking uses the frozen verdict/conclusion precedence and stable finding order; receipt construction uses the canonical policy-version tuple order, lossless refs/sections, and byte-exact digest preimages. Protocol replay/frontier/unknown-event behavior and the canonical fixture mirrors are executable and regression-covered. | Ranking/receipt owners, protocol identity/service-control owners, canonical receipt fixtures, package resource mirrors and focused unit/property/conformance tests |
| Wave B3 port lock (2026-07-19) | The application boundary is frozen as explicit inert port modules with closed records/enums, opaque key and secret capabilities, separate start/check leases, bounded importer/maintenance state, semantic packet limits, and an ADR-009 privacy boundary that rejects unavailable channels before consent or I/O. Adapter/runtime construction remains outside the port package. | `INTERFACES.md`, domain privacy and all port owners, protocol bounds, and focused unit/property/conformance tests |
| Wave B4 adapter lock (2026-07-19) | Configuration, key/object stores, memory/SQLite ledgers, start catalog, importer persistence, maintenance, recovery, migrations, and local Unix transport are materialized with generation-fenced durability and memory/SQLite parity. SQLite reopen reconstructs canonical structural history and verified payloads; importer publication reservations are permanent; check/finding commits advance one canonical ledger; unavailable runtime evidence fails closed. | Config, adapter, migration and resource owners; ledger/importer/privacy port amendments; object/key vectors; cumulative unit/property/conformance/integration tests |
| Wave B5 service lock (2026-07-19) | The trusted local service boundary is materialized with strict ordinary/confidential framing, same-user fixed endpoints, typed clients, vault and unlock generations, durable throttling, one-use human authority, session-triggered relock, one shared ready runtime, structured privacy-safe logging, and bounded daemon drain/close behavior. Privacy receipt inspection is schema-exact and tagged; lifecycle stop has its own closed result. Production ready-application composition remains owned by Wave B6, where the application factory first exists. | Service, runtime/session, observability, control/privacy receipt amendments, focused subprocess security tests, and cumulative non-live gates |
| Wave B6 application/integration lock (2026-07-19) | Every workflow/import/review use case returns a sink-independent internal body; the daemon performs the sole ordinary-client projection from canonical admitted control bytes, catalog-verified route identity, and one durable objectless privacy-audit record. Agent-context provenance widening is limited to verified self-authored material, local-human projection remains a separate sink, and even an all-structural result completes a receipt. START and CHECK crash continuation use one current verified encrypted object pointer; CHECK reopens its lossless frozen case and pinned deterministic findings without rerunning policy or reallocating IDs. Codex JSONL import is stderr-absent only, uses exact durable source digests for review, and never accepts caller-invented commitments. Codex integration remains explicitly unsupported with empty tested profiles until E-002 evidence exists. Maintenance is preview/digest/acceptance bound, machine-bound work never acquires a secret, portable recovery uses a one-shot generation-bound handle, diagnostics are path-free, and invalid/no-op migration targets are rejected. The production service graph starts locked, constructs the application only inside the successful unlock generation, binds authenticated local endpoints after singleton acquisition, and serializes activation/lock/session/stop cleanup. | Application, privacy, importer, integration, service/daemon, ledger/object/migration owners; `INTERFACES.md`; focused unit/property/conformance/integration/subprocess gates |
| Wave B7 client/resource lock (2026-07-19) | CLI and `python -m yoetz` are exact service-only peers with bounded exit/render behavior; confidential commands acquire secrets only through foreground current-user controlling-TTY ceremonies and zero mutable buffers after one-shot use. MCP pre-gates unsupported protocol versions, exposes exactly six projected service tools and four verified static resources, uses frozen descriptor/set digests, emits bounded schema-valid errors/summaries, and keeps stdout transport-pure under partial I/O, EINTR, backpressure, cancellation, and signals. The installed resource manifest verifies 71 entries; its set identity has one explicit runtime-support self-exclusion while the concrete support bytes remain hashed. Artifact and post-build evidence are typed external/absent references, not self-digests; development Codex/MCP capability sets remain empty and unverified. Successor startup removes a stale local endpoint only after singleton acquisition and exact stale-inode proof, and a valid stop response is correlated before EOF closes unrelated pending calls. | CLI/MCP/resource/version/service-client/control owners; guidance/skill/support mirrors; schema/resource manifests; focused conformance/subprocess and cumulative non-live gates |
| Wave C migration lock / W-C-001 (2026-07-19) | Bundle migration `0001` is frozen as one standalone root SQL file with a byte-identical installed resource. Its importer schema owns four tables, including permanent `(publishing_writer_id, request_id)` and `(source_identity_digest, publication_ordinal)` reservations; ordinals `0..batch_count-1` identify batches and ordinal `batch_count` identifies the final report. The runner loads only registered resource bytes, and fresh-schema, constraint, identity, and foreign-key probes pass. | Root/resource migration owners, SQLite migration runner, importer/ledger owners and focused migration tests |
| Wave B8 privacy/egress lock (2026-07-20) | The scripted/local-model/OpenAI provider adapters and the policy-enforcing outbound gateway are materialized: a generation-fenced `ProviderRegistry` snapshot swap gated on policy/vault/human-authority validation, dispatch bound to one exact credential-free factory per attempt, and idempotent terminal transport closure. `ApprovedOutboundCase` carries only its real domain fields (payload bytes, included item IDs, digests); the outbound-case document's `review_packet`/`review_selection_digest` are wire-schema fields, not domain-value attributes. The honesty/operations/surfaces conformance suites close the remaining Wave B/E behavioral gates and, in doing so, found and fixed four real defects: status's future-frontier mapping to `FRONTIER_CONFLICT` instead of the spec's `INVALID_REQUEST`, a status pagination cursor encoding that could never round-trip against `CursorWire`'s pattern, a `candidate_findings` status view that always failed validation on a `JsonObject`-vs-`dict` mismatch, and a respond evidence serialization that sent an explicit null for a field the wire schema requires omitted. Two `FILE_MANIFEST.md` ordering defects and one missing `tests/unit.md` index entry (`test_catalog_audit.py`, `test_local_enforcer.py`) were reconciled; `verify_spec_manifest.py --check` now reports only its own necessarily self-referential prose examples. | Provider adapter, gateway, status, respond, service owners; `INTERFACES.md`; `FILE_MANIFEST.md`/`tests/unit.md`; focused unit/property/integration/conformance tests |

### Later-wave pre-code gate

None. W-C-001 is resolved by the frozen Wave C migration lock above.

No founder-class implementation question remains. Empirical and independent-review gates below
remain release blockers where stated; they do not block building the bounded v0.1 implementation.

### Founder decisions required before implementation freeze

None. Every founder item formerly listed here is resolved below. Empirical release evidence and
the two independent security reviews remain required; closing product choices does not manufacture
that evidence.

### Empirical release-lock gates

| ID | Evidence to freeze | Current posture | Owner/output |
|---|---|---|---|
| E-001 | Release refresh of the 2026-07-17 implementation-locked Python 3.14.6, uv/uv_build 0.11.29, APSW 3.53.3.1/SQLite 3.53.3, dependency, Ruff 0.15.22, Pyright 1.1.411, Node 26.5.0/npm 12.0.1, MCP 1.28.1, and provider SDK pins | Implementation identities are frozen in ADR-007 and the owning repository specs; re-evaluate newest stable versions at release lock without inferring support from a pin. | Dependency refresh review, regenerated `uv.lock` and npm lock, version manifest, exact runtime/capability evidence. |
| E-002 | Exact supported Codex versions and protocol behavior | Implementation now represents Codex skill support with jointly empty capability-profile IDs, supported versions, and hook map, classifying integration as unsupported/incompatible. Local `0.139.0` and names-only `0.144.5` observations advertise nothing; every populated exact cell must first run the critical capability matrix. | ADR-005, capability evidence, support matrix. |
| E-003 | Advertised OS/architecture/filesystem/keyring matrix | macOS arm64 and manylinux x86-64 are candidates only until clean-artifact jobs and restore drills pass. | ADR-003/007, platform capability evidence. |
| E-004 | Ownership heartbeat/stale thresholds, operation/import/check lease durations, writer-queue depth, MCP idle-route cache | Preserve generation fencing regardless of measured durations; choose bounded values from fault/contention runs. | Runtime/storage conformance evidence. |
| E-005 | WAL/checkpoint/page/backup/soak budgets, object-layout scale, and property/state-machine run budgets | Public caps already bound correctness; tune operational thresholds without weakening atomicity or coverage. | Nightly fault/soak evidence and runbooks. |
| E-006 | Argon2id recovery parameters | Calibrate on the slowest advertised profile, record parameters in each artifact, and pass clean-profile restore. | ADR-004 independent review and recovery evidence. |
| E-007 | Exact semantic provider/model/endpoint profile, current provider data-use record, case cap, timeout/retry budget, cost/usage fields | One bound profile only; recommendation eligibility requires current versioned customer-training, retention, human-access, and evidence-digest facts. Unprofiled endpoints remain unavailable and provider credentials never use environment/config/ordinary client channels. **Status (2026-07-22 Issue 6 slice, no flip):** production live-provider composition remains design-gated; the privacy-fenced optional path now has audited not-configured/not-run receipt disclosure (`semantic_review_not_configured` / `semantic_relevance_review_not_run`, distinct from `optional_semantic_review_blocked_by_policy`) and failure degradation that preserves deterministic truth without a false clean. **Status (2026-07-24 provider dispatch slice, no flip):** the four bundled non-official presets (Anthropic, Google Gemini, OpenRouter via OpenAI-compatible Chat Completions; Vercel AI Gateway via Responses) now resolve to real runtime factories instead of `factory_unavailable`, each with an unknown data-use record so none is recommendation-eligible. **Status (2026-07-27 Grok/xAI dogfood slice, no flip):** the exact `xai-openai-chat-completions` profile (`api.x.ai/v1`) is wired through the same Chat Completions factory with an unknown data-use record. xAI's public documentation supports the endpoint and structured `response_format`, but this repository run did not perform an authorized live request or establish current model capability evidence. Whether each host honors strict `response_format` in the installed/runtime combination and whether its default model ID is current stay unrecorded capability facts: configured is not verified, and no release advertises them as working endpoints until this cell runs authorized. | ADR-006/009 opt-in live capability and data-use-profile evidence. |
| E-008 | Release build reproducibility, SBOM/checksum/provenance formats, artifact allowlist, and public-boundary detector vocabulary | No signing claim until a tested end-user verification command exists. | Packaging suite and release workflow evidence. |
| E-009 | Codex skill materiality/activation examples | Freeze examples from bounded dogfood evidence; v0.1 must prefer explicit activation and avoid triggering on trivial work. | Skill fixtures and Codex capability evidence. |
| E-010 | Local service endpoint, peer-credential, permission, lifecycle, keyring, memory-protection, and relock matrix | No platform support claim until a clean-profile service proves authenticated local attachment, locked/ready transitions, crash recovery, suspend/session-lock relock, and secret-canary absence. | Service/control capability evidence and platform matrix. |
| E-011 | Privacy classifier, never-send scanner, minimizer/redactor, consent, endpoint binding, and receipt matrix | No “policy enforced” claim from configuration alone; every profile, channel, scope intersection, denial, and dispatch path must produce exact evidence. Imported Codex command/model text is intentionally verbatim only in encrypted local objects and receives no import-time content scan; every later disclosure crosses the one authoritative classifier/secret scan, tested across shell assignments, inline auth/header flags, bearer/API-key forms, credential URLs, and JSON/UTF-8/chunk splits. | ADR-009 privacy conformance, property, integration, and live-profile evidence. |
| E-012 | Public security, conduct, and support routes | Before public release, prove that private vulnerability reporting is enabled, `security@yoetz.dev` and `conduct@yoetz.dev` are monitored by maintainers, and the repository issue route is available for ordinary support. | Repository policy-link check plus dated maintainer delivery/response drill. |
| E-013 | Exact harness lifecycle trigger points and observation events a hook profile may bind to; context compaction among triggers | Codex `0.144.5` currently exposes `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, and `Stop`, but names alone are not support evidence. A v0.1 exact capability cell may declare a trigger arm and/or a nonempty `observation_events` set only after an installed-artifact run freezes event, payload/privacy boundary, permitted action, coalescing/loop guard, gap codes, and failure behavior for each arm. Observation evidence must prove dual-source ingest (hooks primary; selective session-stream reconciliation), consent/revoke behavior, and that `hook_observed` is earned only from real observation evidence. Unproven cells remain `None` / empty observation arms; unprofiled harnesses stay cooperative-only. | ADR-010 installed-artifact capability evidence and exact harness support-matrix cell. |
| E-014 | Publication-ceremony budget and work-package grouping examples | Dogfood must measure publications per work package, model-authored event bytes, token/latency overhead, abandoned or stale-ledger rate, skipped checks, and user-visible chatter. Large inventories must compare grouped work packages with per-file publication amplification; no threshold is inferred from file count alone. | Harness-neutral capability/conformance fixtures plus bounded dogfood evidence used to freeze guidance examples and budgets. |
| E-015 | Exact structural subject-state capture matrix | No support claim until installed-artifact tests freeze Git/object-format and OS cells, symlink/submodule/racy-worktree behavior, file/byte caps, exclusions, sanitized environment, path/content-free output, and no network or trusted-service reachability. | ADR-011 CLI/subprocess, packaging-boundary, privacy, and capability evidence. |
| E-016 | TOML as alternate nonsecret settings surface, including official OpenAI vs owner-declared OpenAI-compatible HTTPS origin+model | **Working (ADR-014).** Config validates constrained `https_origin`, rejects secrets/free `base_url`, mutual-excludes official vs owner-declared, and writes the same fields from wizard/menu/`yoetz provider endpoint`. Owner-declared data-use defaults to `unknown` (never `assisted`). Privacy desired-state export/apply classifies widen vs tighten and never silently widens. Remaining: optional live owner-declared host probe before advertising verified interoperability beyond the protocol cell. | ADR-014, ADR-006/009 amendments, config/privacy/openai_responses specs, unit fixtures; live probe optional. |

### Independent review blocker

| ID | Required review | Why it blocks release | Exit evidence |
|---|---|---|---|
| R-001 | Independent threat review of ADR-004 key hierarchy, domain separation, envelope header/AAD, wrapping, recovery, and commitment-oracle boundaries | Encryption design errors can silently invalidate privacy and portability claims even when unit tests pass. | Written disposition of every review finding plus updated vectors and clean-profile recovery drill. |
| R-002 | Independent threat review of the service control endpoint, confidential unlock ingress, human-presence authorization, provider credential ingress, policy store, outbound gateway, and egress receipts | A client, local process, adapter, or provider must not bypass the central trust and privacy boundaries merely because each component passes its own tests. | Written disposition of every review finding plus cross-boundary abuse cases and platform evidence. |

### Explicit v0.2 or later deferrals

- Product-telemetry, crash-diagnostics-upload, update-check, and capability-testing transports.
  v0.1 keeps all four channel rows explicit but unavailable/off, rejects enablement with
  `channel_unavailable`, creates no dormant consent, and requires a fresh local-human transition if
  a later exact transport capability is installed.
- Live Git/filesystem artifact inspection during import review; v0.1 compares recorded evidence
  only.
- Chunked import/object formats above the exact 4 MiB source/object cap.
- Codex app-server integration, additional first-party harnesses, and remote service exposure.
  Additional harnesses are additive by construction under ADR-010: an adapter plus a
  `HarnessId` value, with no port, guidance, or registry change. Hooks land on
  `HarnessProfile.hooks_by_capability_profile`, whose exact values distinguish two arms.
  Observation hooks report what the harness saw and are the only arm that makes `hook_observed`
  earnable; for first-party Codex they are a **required v0.1 capability** once the exact cell is
  proven (ADR-010 amendment 2026-07-22), via local `ObservationPort` control methods rather than a
  seventh MCP tool. Trigger hooks fire on a harness lifecycle event — context compaction is the
  motivating case — and prompt the agent to re-ground by calling `status`; they earn no coverage,
  because the `status` result they cause discloses only what that call would already have returned
  under the ordinary provenance rules and the `agent_context` ceiling. An exact v0.1 capability
  cell may declare either or both arms after E-013 passes; unsupported cells remain `None`.
  App-server integration, non-Codex first-party harnesses, and remote service exposure remain
  deferred. No v0.1 adapter silently installs or configures hooks.
- MCP prompts. v0.1 ships tools, resources, and the `instructions` string only.
- Launchd/systemd convenience installers, multi-user service hosting, remote control, and
  independent concurrent service writers; the single-user persistent local service and
  authenticated local IPC are v0.1 requirements.
- Public `doctor`/support-bundle command and schema; v0.1 has `version --json` plus startup gates.
- Broad waiver scopes, noninteractive/model waivers, or waiver of deterministic policy classes.
- Structural task/workflow status without keys; the locked service exposes only its bounded service
  lifecycle/reason status, while all six task operations fail closed when required keys are locked.
- Generic headless passphrase input and inherited-secret descriptors. v0.1 unattended readiness
  uses only an existing vault through a release-tested OS keyring; passphrase mode requires the
  confidential human ceremony. **Scoped exception (ADR-015/016):** cloud-agent elevated consent may
  use inherited FDs for catalogued `secret_ingress` / `secret_reauth` operations after exact
  digest-bound human phrase confirmation (implemented: vault initialize + provider credential
  set/rotate). Phrase-only irreversible ops are catalogued with `implemented=false` until durable
  grant consumption exists at owning CLIs. This is not a standing `--yolo`, general headless vault
  API, or unlock path for an already-locked vault.
- A native vault broker/subprocess; v0.1 uses the in-service `SecretMemoryPort` boundary.
- Sigstore or other signing claims until verification is documented and tested.
- A combined rendered skill handbook; v0.1 ships the two separately owned reference documents.
- Global/user Codex skill installation scope; v0.1 mutates only one explicitly selected trusted
  project after preview and confirmation.
- In-place repair of a quarantined route; v0.1 recovery builds/verifies a new target and switches
  routes through the catalog state machine.

### Resolved founder and working decisions reflected across the tree

- Ignored architecture/strategy files are private drafting inputs only; the committed ADRs,
  `docs/INTERFACES.md`, and the public code and tests are self-contained public authority.
- Public schema `$id` values are real immutable hosting routes below
  `https://schemas.yoetz.dev/0.1/`. The checked-in `schemas/` tree deploys byte-for-byte at that
  prefix, the local gate resolves the complete manifest with networking disabled, and the release
  gate publishes then re-fetches the same bytes. Hosted availability never becomes a runtime
  dependency.
- **F-001:** Yoetz uses the unmodified official Apache License 2.0 text and the exact SPDX expression
  `Apache-2.0`. v0.1 does not invent or require a project-wide copyright-holder notice; applicable
  ownership and repository history remain intact.
- **F-002:** The project adopts Contributor Covenant 3.0. T3 Code may inspire the clarity and
  contributor experience of repository documents, but it is not an authority for Yoetz runtime,
  protocol, privacy, or security architecture.
- **F-005:** The official npm Pyright package remains an exactly pinned contributor/CI tool only.
  Node/npm are not Yoetz runtime requirements. The formerly deferred `npx yoetz` launcher is now
  built as its own reviewed distribution surface under ADR-012: a dependency-free delegation-only
  package at `support/npm-launcher/` pinned to the exact PyPI version, kept deliberately
  unpublished (`"private": true`) until a separate publication decision.
- **F-020 (ADR-012, 2026-07-21):** Founder-authorized first-run setup wizard. `yoetz setup
  run|status` and `yoetz integrate <harness> mcp status|preview|install` automate Codex discovery
  and the runbook's check-then-add MCP registration behind preview→confirm→execute; bare `yoetz`
  on an interactive terminal with no completion marker launches the wizard once (every non-TTY
  bare invocation still prints help). Foreign same-name MCP entries are preserved, never
  replaced; privacy setup and provider credentials remain their existing trusted ceremonies, which
  the wizard points to but never automates.
- **F-006:** Private vulnerability reporting uses GitHub's private vulnerability-reporting surface
  plus `security@yoetz.dev`; private conduct reports use the distinct `conduct@yoetz.dev` route;
  ordinary support and bug reports use repository issues. E-012 verifies these routes before
  release rather than treating prose as operational proof.
- The persistent per-user local service is in v0.1 and is the sole owner of keys, decrypted state,
  durable writers, privacy policy enforcement, and outbound dispatch. CLI, MCP, and UI are clients.
- **F-007:** v0.1 keeps vault keys, provider-credential handles, and decrypted state inside the
  trusted local Yoetz service behind swappable `VaultPort`/`SecretMemoryPort` boundaries. Verified
  page-lock/no-core protections and best-effort overwrite are used where available; a native vault
  broker is deferred. This is a Yoetz-specific decision, not an inheritance from T3 Code.
- **F-008:** Unattended readiness is required and is provided in v0.1 by automatic unlock of an
  existing vault through an approved, release-tested OS keyring. If that path is unavailable or
  locked, the service remains alive in explicit `locked`; passphrase vaults require the confidential
  human ceremony. Arguments, environment, config, stdin, files, MCP, transcripts, and LLM context
  are not passphrase-ingress channels.
- **F-009:** v0.1 permits only reviewed bundled provider adapters from a closed registry as trusted
  in-process service code. They receive an approved case and one-attempt transport capability, not
  ambient repositories or state. This is capability minimization, not an OS sandbox.
- **F-010:** Pristine automatic keyring initialization requires both verified keyring create/load
  support and an action-bound `UserPresencePort` for the exact release cell. Otherwise Yoetz writes
  no immutable keyring-mode state and offers explicit passphrase initialization. Existing keyring
  vaults may unlock for permitted local work, but durable widening and credential mutation remain
  fenced without strong presence. `HumanAuthorityCapability.source=unavailable` is not a second
  authorization gate for an already reauthenticated exact local-model policy row; that row remains
  independently fenced by classifier, exact profile, generation, and `consume_local` checks.
- **F-011:** `confirm_every_request` uses a foreground exact prepared-case preview and one explicit
  decision for one physical attempt inside existing durable policy. Every retry needs a new decision;
  durable widening and credential mutation still require strong action-bound reauthentication.
- **F-012:** Candidate/user/repository/config/transcript credentials remain non-overridable
  never-send data. A separately provisioned vault credential may be consumed only once as
  authentication metadata for the exact profile-bound HTTPS endpoint selected by the reviewed
  registry, using platform CA trust and hostname validation, and never enters model content,
  previews, receipts, logs, config, environment, transcripts, or reusable SDK state. v0.1 does not
  claim certificate or SPKI pinning.
- **F-013:** Yoetz may connect only to an exact approved owner-only AF_UNIX local-model endpoint and
  does not launch/download the model or perform DNS/IP networking. The separate runtime receives
  plaintext and is therefore an explicitly trusted disclosure sink unless its exact support cell
  proves enforceable network isolation; Yoetz does not claim away another process's authority.
- **F-014:** Keep the zero-egress installation seed, but make `assisted` the upstream CLI's
  recommended review-context recipe after an explicit technical-user setup decision. It uses a
  standing workspace policy, public-structural plus ordinary-user-content classes, rich goal/
  obligation/claim/timeline/deterministic-basis context, bounded problem-local excerpts already
  recorded in the frozen case, and an exact endpoint whose current data-use record states training
  `prohibited`, retention `none|bounded`, and provider human access `prohibited|restricted`. The
  recipe's editable current-evidence guard is on. Known-broad, unknown, or stale posture removes the
  recommendation; an explicit custom loosening may turn the guard off without retaining that claim. It
  does not prompt per ordinary check/retry. Users may choose stricter, broader, custom, or forked
  behavior; never-send and scope remain non-overridable for upstream-conforming builds.
- **F-015:** Harness integration is a port and Codex is its first adapter (ADR-010). Agent guidance
  is owned once, harness-neutrally, under `guidance/` with exactly one packaged copy;
  `IntegrationsPort` is parameterized by a closed `HarnessId` (v0.1: exactly `codex`) plus a
  reviewed `HarnessProfile`. Adding a first-party harness is one `HarnessId` value plus one adapter
  and requires no port, registry, guidance, or schema change, so a fork can do it without touching
  the core. `HarnessProfile.hooks_by_capability_profile` binds every exact profile ID to either
  `None` or, after E-013 passes, a descriptor that may declare a trigger arm and/or a nonempty
  closed `observation_events` set. For first-party Codex, observation is a required v0.1
  capability once capability-proven: it earns `hook_observed` only from real observation evidence,
  through local `ObservationPort` control (not a seventh MCP tool). Trigger-only cells remain valid
  recovery ergonomics without raising coverage. Unproven cells stay `None` / empty observation
  arms and keep cooperative coverage honest.
- **F-021 (2026-07-22):** First-party Codex observation is in protocol `0.1` (not deferred to
  v0.2). Dual sources are hooks (primary, low-latency) and selective session-stream reconciliation.
  Shared types are `ObservationSource`, `ObservationEnvelope`, `ObservationCursor`,
  `ObservationStatus`, and `AdviceSnapshot`. Observation consent is one project-level confirmation
  via a private workspace commitment, separate from egress consent; revocation stops new ingestion
  and retains already-kept evidence. `AdviceSnapshot` surfaces via nonblocking hooks and ordinary
  `status`. Batch `ImporterPort` JSONL import stays a separate support surface. Existing v0.1 data
  remains readable; migrations may add only observation consent/cursor/dedup/advice state.
- **F-016:** Any MCP host is supported with no integration. `guidance/agent-instructions.md` is
  served verbatim as the initialize `instructions` string to every host and must carry every rule
  whose absence would cause harm, because it is the only tier guaranteed to arrive; the four
  guidance documents are also exposed as read-only `yoetz://guidance/<name>` resources. MCP declares
  tools and resources only. `mcp/descriptors.py` owns every agent-read string, loads it from verified
  packaged resources with no runtime composition or fallback, and is bound by the guidance wording
  lint. An unprofiled host therefore earns `cooperative_mcp`/`self_asserted`/`published_only` — the
  weakest honest coverage — rather than a warned-about degraded mode.
- **F-017:** `ClientInfoModel.kind` gains the transport-neutral `cooperative_agent` value so every
  harness has exactly one honest identity and no unprofiled host must misreport itself as
  `codex_cli` or `test_client`. `kind` is provenance only: assurance derives from the integration
  channel, so no `kind` value participates in `Coverage`, and recognising a harness first-party stays
  additive.
- **F-018:** Local client disclosure splits by audience. Ordinary human-readable rendering to an
  attached controlling terminal resolves to a new `local_human_view` sink and is not gated by the
  agent ceiling: a local human reading a vault they unlocked is not a third-party disclosure, and
  gating their own terminal protects nobody. `--json`, non-TTY or redirected streams, and every
  `mcp_bridge` client resolve to `agent_context`; the client never selects its own sink. Terminal
  emulation by a same-UID process is the stated threat-model limit already bounding the unlock TTY
  contract.
- **F-019:** `agent_context` becomes provenance-conditional on a computed, closed
  `DisclosureProvenance`. Material the requesting writer authored at the frozen frontier, and kernel
  prose derived solely from it, project without a category grant, because that content already sits
  in the host's context and withholding it protects nothing while breaking the
  `check → respond → recheck` loop on the default install. Other-writer material, imports, and
  provider-derived semantic prose still require the explicit grant. Provenance is computed from the
  ledger and never asserted; ambiguity denies; `sensitive_confidential` and every never-send kind
  stay absolute under every provenance. This amends the ADR-009 boundary and is recorded there.
- Every outbound provider request follows classification, policy intersection, local
  minimization/redaction/secret scanning, optional trusted-human preview of the exact prepared
  case, gateway revalidation, and binding to one provider/model/endpoint profile. Network channels
  remain independently consented.
- `semantic_required` requires semantic success for a complete verdict, not for returning useful
  local truth: every post-deterministic provider absence, policy denial, refusal, timeout, invalid,
  stale, late, or exhausted outcome returns the deterministic findings with verdict
  `incomplete_check`, explicit reason, and no semantic findings.
- ~~Every future source, resource, fixture, test, script, workflow, and public document has one
  owning natural-language file spec before implementation begins.~~ **Superseded 2026-07-25:** the
  spec-first tree completed its purpose — all 626 declared files were built — and was retired rather
  than maintained as a second copy of a shipped system. The authority chain is now ADR →
  `docs/INTERFACES.md` → code and tests. The final tree is recoverable at tag `specs-tree-final`.
- The public workflow has exactly six operations; import/review, backup/restore/migrate,
  integration, version, and MCP serving are bounded support surfaces rather than extra MCP tools.
- Candidate findings are ID-free pure-kernel values; the application allocates and durably pins
  finding IDs before publication.
- Coverage unordered dimensions are sorted unique sets; semantic terminal states distinguish
  `late` from dependency-`stale` and never use an ambiguous `completed` state.
- Receipts summarize a frozen subject frontier, receive IDs/time/version inputs explicitly, and
  use exactly three bounded conclusions.
- v0.1 waiver scope is one finding, authorized only by an explicitly confirmed interactive local
  human flow.
- Import source identity uses a keyed commitment plus exact Codex capability profile and mapping
  version; the ordinary source digest remains inside encrypted audit material.
- The ordinary v0.1 import request requires stderr absent with exact false/zero constants; it
  accepts neither raw stderr nor a caller-created stderr commitment.
- Version manifests serialize optional components as `{status: "absent"}` and expose a bounded
  resource summary by default; the full resource list requires the explicit resources option.
- Alpha Codex support is an explicit tested-version set, never an inferred continuous range;
  `release-probe` is CI/environment/CLI-only and invalid in user config.
- `common/operation-result-1.0.0.schema.json` is a separately public language-neutral schema, and
  the public-claim map is committed as canonical JSON.
- v0.1 uses exactly the registered `ObjectKind` vocabulary, a one-level two-hex object shard, and a
  24-hour orphan safety window.
- Every mutating maintenance operation requires an exact digest-bound interactive confirmation in
  v0.1; no destination history silently waives it.

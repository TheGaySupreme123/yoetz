# Yoetz v0.1 — decision ledger and implementation-freeze gates

**Wave:** A–F | **ADRs:** ADR-001 through ADR-009 | **Imports (spec-tree):** all owning specs |
**Imported by:** `specs/README.md`, ADR ratification, implementation-freeze review

## Purpose

Keep unresolved choices visible without scattering founder questions, empirical calibration, and
deferred features through hundreds of files. Owning specs may explain a local uncertainty, but this
file is the one queue used to decide whether the complete natural-language build is frozen.

## Public surface

Every item has a stable ID, class (`founder`, `empirical`, `independent-review`, or `deferred`), a
working default, an owner, and a freeze condition. An item is removed only by recording the answer
in its owning ADR/spec and moving a short result into the resolved-decisions section below.

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
  requires an ADR/spec amendment.
- A deferred feature cannot be implemented opportunistically while adjacent code is being built.

## Invariants

1. Ignored architecture/strategy inputs are never required to interpret an item or its answer.
2. Founder questions describe product/legal choices, not measurements engineers can answer.
3. Release claims remain narrower than the evidence produced by the applicable gate.
4. Closing an item updates every affected owning spec and fixture in the same review.

## Tests

`scripts/scan_public_boundary.py` rejects private drafting dependencies; the file-manifest check
requires every named owning file to exist; release-evidence generation refuses open applicable
empirical/review gates.

## Open questions

### Founder decisions required before implementation freeze

None. Every founder item formerly listed here is resolved below. Empirical release evidence and
the two independent security reviews remain required; closing product choices does not manufacture
that evidence.

### Empirical release-lock gates

| ID | Evidence to freeze | Current posture | Owner/output |
|---|---|---|---|
| E-001 | Exact newest stable Python, uv, APSW/SQLite, dependency, Ruff, Pyright, npm/Node, MCP SDK, provider SDK pins | Candidate values in ADRs are drafting inputs; refresh at implementation lock and again at release lock. | Dependency refresh review, `uv.lock`, npm lock, version manifest. |
| E-002 | Exact supported Codex versions and protocol behavior | No continuous range is inferred; every advertised version must run the critical capability matrix. | ADR-005, capability evidence, support matrix. |
| E-003 | Advertised OS/architecture/filesystem/keyring matrix | macOS arm64 and manylinux x86-64 are candidates only until clean-artifact jobs and restore drills pass. | ADR-003/007, platform capability evidence. |
| E-004 | Ownership heartbeat/stale thresholds, operation/import/check lease durations, writer-queue depth, MCP idle-route cache | Preserve generation fencing regardless of measured durations; choose bounded values from fault/contention runs. | Runtime/storage conformance evidence. |
| E-005 | WAL/checkpoint/page/backup/soak budgets, object-layout scale, and property/state-machine run budgets | Public caps already bound correctness; tune operational thresholds without weakening atomicity or coverage. | Nightly fault/soak evidence and runbooks. |
| E-006 | Argon2id recovery parameters | Calibrate on the slowest advertised profile, record parameters in each artifact, and pass clean-profile restore. | ADR-004 independent review and recovery evidence. |
| E-007 | Exact semantic provider/model/endpoint profile, current provider data-use record, case cap, timeout/retry budget, cost/usage fields | One bound profile only; recommendation eligibility requires current versioned customer-training, retention, human-access, and evidence-digest facts. Unprofiled endpoints remain unavailable and provider credentials never use environment/config/ordinary client channels. | ADR-006/009 opt-in live capability and data-use-profile evidence. |
| E-008 | Release build reproducibility, SBOM/checksum/provenance formats, artifact allowlist, and public-boundary detector vocabulary | No signing claim until a tested end-user verification command exists. | Packaging suite and release workflow evidence. |
| E-009 | Codex skill materiality/activation examples | Freeze examples from bounded dogfood evidence; v0.1 must prefer explicit activation and avoid triggering on trivial work. | Skill fixtures and Codex capability evidence. |
| E-010 | Local service endpoint, peer-credential, permission, lifecycle, keyring, memory-protection, and relock matrix | No platform support claim until a clean-profile service proves authenticated local attachment, locked/ready transitions, crash recovery, suspend/session-lock relock, and secret-canary absence. | Service/control capability evidence and platform matrix. |
| E-011 | Privacy classifier, never-send scanner, minimizer/redactor, consent, endpoint binding, and receipt matrix | No “policy enforced” claim from configuration alone; every profile, channel, scope intersection, denial, and dispatch path must produce exact evidence. | ADR-009 privacy conformance, property, integration, and live-profile evidence. |
| E-012 | Public security, conduct, and support routes | Before public release, prove that private vulnerability reporting is enabled, `security@yoetz.dev` and `conduct@yoetz.dev` are monitored by maintainers, and the repository issue route is available for ordinary support. | Repository policy-link check plus dated maintainer delivery/response drill. |
| E-013 | Exact harness lifecycle trigger points a trigger hook may bind to, context compaction among them | Which hooks a harness actually exposes is capability evidence, not a spec choice: an installed-artifact capability run must freeze the exact lifecycle events, their payload, and what a hook may do at each before any trigger hook ships. v0.1 declares no hooks of either arm. | ADR-010 installed-artifact capability evidence and harness support matrix. |

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
- Codex hooks or app-server integration, additional first-party harnesses, and remote service
  exposure. Additional harnesses are additive by construction under ADR-010: an adapter plus a
  `HarnessId` value, with no port, guidance, or registry change. Hooks land on
  `HarnessProfile.hooks`, which distinguishes two arms. Observation hooks report what the harness
  saw and are the only deferred capability that would make `hook_observed` earnable. Trigger hooks
  fire on a harness lifecycle event — context compaction is the motivating case — and prompt the
  agent to re-ground by calling `status`; they earn no coverage, because the `status` result they
  cause discloses only what that call would already have returned under the ordinary provenance
  rules and the `agent_context` ceiling. E-013 must freeze the exact lifecycle trigger points a
  harness exposes before any trigger hook ships. v0.1 declares neither arm.
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
  confidential human ceremony.
- A native vault broker/subprocess; v0.1 uses the in-service `SecretMemoryPort` boundary.
- A public npm/`npx yoetz` launcher. v0.1 remains a Python distribution with `uv` as its supported
  install and tool runner; any npm launcher needs its own provenance, delegation, upgrade, and
  platform contract.
- Sigstore or other signing claims until verification is documented and tested.
- Hosted retrieval for public schema `$id` URLs; v0.1 resolves the frozen schema set offline.
- A combined rendered skill handbook; v0.1 ships the two separately owned reference documents.
- Global/user Codex skill installation scope; v0.1 mutates only one explicitly selected trusted
  project after preview and confirmation.
- In-place repair of a quarantined route; v0.1 recovery builds/verifies a new target and switches
  routes through the catalog state machine.

### Resolved founder and working decisions reflected across the tree

- Ignored architecture/strategy files are private drafting inputs only; the committed ADRs,
  `INTERFACES.md`, and owning specs are self-contained public authority.
- **F-001:** Yoetz uses the unmodified official Apache License 2.0 text and the exact SPDX expression
  `Apache-2.0`. v0.1 does not invent or require a project-wide copyright-holder notice; applicable
  ownership and repository history remain intact.
- **F-002:** The project adopts Contributor Covenant 3.0. T3 Code may inspire the clarity and
  contributor experience of repository documents, but it is not an authority for Yoetz runtime,
  protocol, privacy, or security architecture.
- **F-005:** The official npm Pyright package remains an exactly pinned contributor/CI tool only.
  Node/npm are not Yoetz runtime requirements, and a public `npx yoetz` launcher is deferred as a
  separate distribution surface rather than hidden inside the type-checker decision.
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
  fenced without strong presence.
- **F-011:** `confirm_every_request` uses a foreground exact prepared-case preview and one explicit
  decision for one physical attempt inside existing durable policy. Every retry needs a new decision;
  durable widening and credential mutation still require strong action-bound reauthentication.
- **F-012:** Candidate/user/repository/config/transcript credentials remain non-overridable
  never-send data. A separately provisioned vault credential may be consumed only once as
  authentication metadata for the exact pinned TLS endpoint and never enters model content,
  previews, receipts, logs, config, environment, transcripts, or reusable SDK state.
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
  the core. `HarnessProfile.hooks` is declared and `None` for every v0.1 harness, keeping
  `hook_observed` unearnable in v0.1 and making hooks a later profile capability rather than a
  second port.
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
- Every future source, resource, fixture, test, script, workflow, and public document has one owning
  natural-language file spec before implementation begins.
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
  version; ordinary source/stderr digests remain inside encrypted audit material.
- Raw stderr is not retained in v0.1; only bounded count/truncation and a commitment over the
  retained prefix survive structurally.
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

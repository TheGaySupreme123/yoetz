# Yoetz Core v0.1 — decision ledger and implementation-freeze gates

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

| ID | Decision | Working default and recommendation | Owning files | Freeze condition |
|---|---|---|---|---|
| F-001 | Apache release copyright-holder name | **Apache-2.0 is selected.** The only remaining legal metadata choice is the exact person or entity named in the release copyright notice; no alternative license remains open. | `repository/LICENSE.md`, `repository/pyproject.toml.md`, `repository/README.md` | Founder supplies the exact public copyright-holder spelling. |
| F-002 | Community standard | Adopt **Contributor Covenant 2.1**, with a real non-security enforcement contact distinct from the security intake. | `repository/CODE_OF_CONDUCT.md`, `repository/CONTRIBUTING.md`, `repository/SECURITY.md` | Founder selects the standard and public contact routing. |
| F-005 | Development-only Node toolchain | Keep ADR-007's official npm Pyright pin in private `package.json`/lock files; Node is a contributor/CI prerequisite only and never an end-user/runtime surface. | ADR-007, `repository/package.json.md`, `repository/package-lock.json.md` | Founder ratifies or chooses a non-npm type checker and amends ADR-007. |
| F-006 | Public contact routing | Name maintained public routes for security disclosure, community conduct, and general support; security and conduct intake must remain distinct. | `repository/SECURITY.md`, `repository/CODE_OF_CONDUCT.md`, quarantine runbook | Founder supplies the real channels before public release. |
| F-007 | v0.1 decrypted-memory boundary | Recommended working rule: keep vault keys and decrypted state inside the one trusted local service behind swappable `VaultPort`/`SecretMemoryPort` boundaries, use platform page-lock/no-core capabilities where verified, and make only best-effort overwrite claims. A native broker/subprocess is the stronger but materially larger alternative. | ADR-004, ADR-008, `ports/keys.md`, `ports/secret_memory.md`, service capability tests | Founder accepts the in-service v0.1 boundary or requires a native vault broker before public alpha. |
| F-008 | Headless passphrase unlock | Recommended working rule: defer inherited-secret-descriptor support. A headless v0.1 service can become ready through an approved unlocked OS keyring; otherwise it remains explicitly `locked`. The alternative is a narrowly scoped, one-shot inherited descriptor protocol with new lifecycle and leak tests. | ADR-004, ADR-008, service lifecycle and capability specs | Founder accepts deferral or explicitly requires the descriptor design in v0.1. |
| F-009 | Provider-adapter isolation boundary | Recommended working rule: v0.1 loads only reviewed bundled adapters as trusted in-process service code. Composition passes an approved case and exact one-attempt transport capabilities, never repository/storage/environment handles; third-party and dynamic adapters are absent. Public claims must say this is not an OS sandbox and does not resist a malicious adapter already executing inside the trusted service. The stronger alternative is a least-authority sandboxed provider subprocess/broker with executable escape and ambient-access tests. | ADR-006, ADR-009, `adapters/privacy/gateway.md`, `adapters/providers/`, `repository/PRIVACY.md`, public claim map | Founder accepts bundled adapters inside the trusted computing base or requires sandboxed provider execution before public alpha. |
| F-010 | Durable local-human authority in keyring mode | Current safe default: pristine automatic keyring initialization requires both verified create/load keyring support and an artifact-verified action-bound `UserPresencePort` for the exact release cell. Without both, no keyring/vault artifact or immutable mode is created; the service remains `uninitialized/locked` with `human_authority_unavailable`, and a local human may explicitly choose passphrase setup. Existing keyring vaults may remain ready-local without current presence, but external activation, policy widening, provider-credential set/rotate, and other durable authority changes stay fenced. Ordinary keyring unlock, TTY presence, and same-UID identity are insufficient. Alternatives are additional exact macOS/Linux presence adapters, enrollment/recovery of a separate Yoetz admin-authorization secret, or a reviewed first-install/migration design. | ADR-008, ADR-009, `ports/secret_memory.md`, `service/human_control.md`, setup wizard and platform capability specs | Founder selects the v0.1 authority source and supported platform/migration behavior; any secret option receives a complete setup, recovery, rotation, and leak contract before implementation. |
| F-011 | Per-request confirmation strength | Recommended working rule: `confirm_every_request` uses an exact prepared-case preview plus explicit foreground `/dev/tty` approve/deny for a disclosure already inside the durable authorized policy. One decision binds exactly one physical attempt; every retry requires a fresh proposal, preview, and decision. It cannot widen policy and does not mint reusable authority, but it is intent/UX evidence rather than cryptographic human proof against malicious same-UID code with arbitrary shell/socket access. Strong OS/admin-secret reauthentication remains mandatory for durable widening and credential changes. The stronger but higher-friction alternative requires action-bound reauthentication for every external request. | ADR-009, `service/confidential_protocol.md`, `service/human_control.md`, `application/egress.md`, privacy wizard/fixtures/tests | Founder accepts foreground digest-bound consent inside the existing ceiling or requires strong reauthentication on every confirmed request. |
| F-012 | Provider credential versus never-send wording | Recommended working interpretation: credentials discovered in candidate/user/repository/config/transcript content remain non-overridable never-send data. A separately and confidentially provisioned service-vault `ProviderCredentialHandle` may be emitted only as one-attempt authentication metadata to the exact pinned TLS provider endpoint; it never enters model body/context, preview, receipt, log, environment, config, or reusable SDK state. Encryption, vault-unlock, and recovery material have no exception. The literal alternative forbids all credential egress and therefore all credentialed external providers. | ADR-006, ADR-009, `domain/privacy.md`, `ports/secret_memory.md`, provider/gateway specs, `repository/PRIVACY.md` | Founder confirms the control-plane authentication exception or selects no credentialed external providers. |
| F-013 | Local-model runtime trust boundary | Recommended working rule: Core itself connects only to an exact approved AF_UNIX endpoint and performs no model launch, download, DNS, or IP networking. A pre-existing local-model runtime that receives plaintext is nevertheless an explicitly trusted local disclosure sink unless its support cell proves enforceable no-network sandboxing; Core must not claim that another same-UID process lacks ambient network authority. Alternatives are a Core-managed verifiably network-denied runtime or no local-model support under the strongest local-only claim. | ADR-009, `adapters/providers/local_model.md`, `repository/PRIVACY.md`, setup wizard, public claim map and capability tests | Founder accepts the named local runtime as part of the trusted local computing base, requires managed sandboxing, or omits local-model support from v0.1. |

### Empirical release-lock gates

| ID | Evidence to freeze | Current posture | Owner/output |
|---|---|---|---|
| E-001 | Exact newest stable Python, uv, APSW/SQLite, dependency, Ruff, Pyright, npm/Node, MCP SDK, provider SDK pins | Candidate values in ADRs are drafting inputs; refresh at implementation lock and again at release lock. | Dependency refresh review, `uv.lock`, npm lock, version manifest. |
| E-002 | Exact supported Codex versions and protocol behavior | No continuous range is inferred; every advertised version must run the critical capability matrix. | ADR-005, capability evidence, support matrix. |
| E-003 | Advertised OS/architecture/filesystem/keyring matrix | macOS arm64 and manylinux x86-64 are candidates only until clean-artifact jobs and restore drills pass. | ADR-003/007, platform capability evidence. |
| E-004 | Ownership heartbeat/stale thresholds, operation/import/check lease durations, writer-queue depth, MCP idle-route cache | Preserve generation fencing regardless of measured durations; choose bounded values from fault/contention runs. | Runtime/storage conformance evidence. |
| E-005 | WAL/checkpoint/page/backup/soak budgets, object-layout scale, and property/state-machine run budgets | Public caps already bound correctness; tune operational thresholds without weakening atomicity or coverage. | Nightly fault/soak evidence and runbooks. |
| E-006 | Argon2id recovery parameters | Calibrate on the slowest advertised profile, record parameters in each artifact, and pass clean-profile restore. | ADR-004 independent review and recovery evidence. |
| E-007 | Exact semantic provider/model/endpoint profile, case cap, timeout/retry budget, cost/usage fields | One bound profile only; unprofiled endpoints remain unavailable and provider credentials never use environment/config/ordinary client channels. | ADR-006/009 opt-in live capability evidence. |
| E-008 | Release build reproducibility, SBOM/checksum/provenance formats, artifact allowlist, and public-boundary detector vocabulary | No signing claim until a tested end-user verification command exists. | Packaging suite and release workflow evidence. |
| E-009 | Codex skill materiality/activation examples | Freeze examples from bounded dogfood evidence; v0.1 must prefer explicit activation and avoid triggering on trivial work. | Skill fixtures and Codex capability evidence. |
| E-010 | Local service endpoint, peer-credential, permission, lifecycle, keyring, memory-protection, and relock matrix | No platform support claim until a clean-profile service proves authenticated local attachment, locked/ready transitions, crash recovery, suspend/session-lock relock, and secret-canary absence. | Service/control capability evidence and platform matrix. |
| E-011 | Privacy classifier, never-send scanner, minimizer/redactor, consent, endpoint binding, and receipt matrix | No “policy enforced” claim from configuration alone; every profile, channel, scope intersection, denial, and dispatch path must produce exact evidence. | ADR-009 privacy conformance, property, integration, and live-profile evidence. |

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
- Codex hooks or app-server integration, additional harnesses, and remote service exposure.
- Launchd/systemd convenience installers, multi-user service hosting, remote control, and
  independent concurrent service writers; the single-user persistent local service and
  authenticated local IPC are v0.1 requirements.
- Public `doctor`/support-bundle command and schema; v0.1 has `version --json` plus startup gates.
- Broad waiver scopes, noninteractive/model waivers, or waiver of deterministic policy classes.
- Structural task/workflow status without keys; the locked service exposes only its bounded service
  lifecycle/reason status, while all six task operations fail closed when required keys are locked.
- Generic headless passphrase input and inherited-secret descriptors unless F-008 changes the v0.1
  boundary.
- A native vault broker/subprocess unless F-007 makes it a public-alpha prerequisite.
- Sigstore or other signing claims until verification is documented and tested.
- Hosted retrieval for public schema `$id` URLs; v0.1 resolves the frozen schema set offline.
- A combined rendered skill handbook; v0.1 ships the two separately owned reference documents.
- Global/user Codex skill installation scope; v0.1 mutates only one explicitly selected trusted
  project after preview and confirmation.
- In-place repair of a quarantined route; v0.1 recovery builds/verifies a new target and switches
  routes through the catalog state machine.

### Resolved working decisions already reflected across the tree

- Ignored architecture/strategy files are private drafting inputs only; the committed ADRs,
  `INTERFACES.md`, and owning specs are self-contained public authority.
- Apache-2.0 is the selected public license; only the copyright-holder string remains to fill.
- The persistent per-user local service is in v0.1 and is the sole owner of keys, decrypted state,
  durable writers, privacy policy enforcement, and outbound dispatch. CLI, MCP, and UI are clients.
- If an approved OS keyring cannot unlock the vault, the service remains alive in explicit
  `locked`; ordinary CLI/MCP arguments, environment, configuration, logs, transcripts, and LLM
  context are forbidden secret-ingress channels.
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

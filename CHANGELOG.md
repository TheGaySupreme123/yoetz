# Changelog

All notable user-visible changes to Yoetz are documented in this file. Format is a lightweight,
project-native heading style that marks the pending version as unreleased above
reverse-chronological released versions.

## 0.1.0 — Public alpha (unreleased)

Planned initial public alpha release. No version has shipped before it, so every entry below
describes behavior intended for the first release rather than a change from a previous release.

### Added

- Typed evidence digest provenance (`evidence_recorded/1.1.0`) records the exact closed byte
  subject, availability, byte count, and authority that established each new digest. Kind/subject
  contradictions fail closed; approved checks publish bounded service-owned receipts; legacy,
  digest-only, withheld, redacted, and semantically omitted evidence remain explicit limitations
  without changing frozen `evidence_recorded/1.0.0` bytes (ADR-020).

- The six-operation protocol (`start`, `publish_work`, `check`, `respond`, `status`, `receipt`) over
  both the CLI and MCP, with identical request/result contracts and a shared canonical
  encoding/idempotency model.
- A persistent, per-user local service that is the sole owner of encryption keys, decrypted state,
  and SQLite writer connections, reached over an authenticated local control protocol; CLI and MCP
  are bounded clients of it.
- Local encrypted object storage (`yoetz-object/1`) and an installation vault with OS-keyring and
  explicit passphrase initialization modes.
- Generation-fenced single-writer durability for the installation catalog and every task bundle,
  built on APSW/SQLite with WAL and verified build/PRAGMA checks.
- A centrally enforced privacy and data-egress protocol: classification, policy resolution, local
  minimization/redaction/secret scanning, optional human preview/approval, and a structural
  `EgressReceipt` for every reserved decision and physical attempt. The durable product default is
  `local_only` with structural package update checks on (opt-out) and no task-content egress;
  true zero-network requires turning `update_checks` off as well.
- Interactive package-update advisory (TUI first-run finish, resume tip, `/doctor`) and
  upgrade-over-same-version-reinstall prompts on setup/`/connect`, gated by the independent
  `update_checks` channel against an allowlisted PyPI identity URL. Upgrade remains a human-run
  `uv tool upgrade yoetz` command; work receipts are unchanged.
- Widening the privacy policy is authorized only at the reauthenticated trusted terminal, which
  renders the complete `before → after` diff of every security-relevant field the proposal moves —
  destination, disclosed information, confirmation, authorization scope, limits, and local
  visibility — derived from the same comparison that classifies the change as a widening. The diff
  digest is shown as integrity evidence and labelled as such rather than as the description.
- Privacy setup opens on one recommended policy — Assisted review for an exact provider route with
  current reviewed no-training evidence and retention no longer than 30 days, Private otherwise —
  with its trade-off stated. Accepting it asks nothing further; declining opens the named recipes
  (Private, Metadata only, Assisted review, Expanded review, Custom), and only Custom configures
  individual settings, in five grouped sections. The terminal interface's `/privacy` uses the same
  rule and the same recipe names, and selects rather than authorizes.
- `yoetz privacy pending` lists the disclosure decisions awaiting a local human, with their expiry
  and nothing about what they would disclose. `privacy decide-disclosure` needs an exact pending id
  that normally arrives in the check result waiting on it; this is how that ceremony is found again
  when the id is lost. It is a CLI/UI-only ordinary control method and is not reachable over MCP.
- First run offers semantic review first and pre-selects it, in both the prompt-loop wizard and the
  terminal interface. This is a recommendation about that question only: every installation still
  seeds zero-egress `local_only`, the answer binds no provider and commits no policy, and local-only
  remains one keystroke away. The answer is now taken before the Codex MCP registration, and decides
  which route is registered.
- An optional, privacy-gated semantic review path behind the same gateway, with a reviewed OpenAI
  Responses adapter, a local-model AF_UNIX profile, and a scripted fake provider for testing.
- Codex integration as the first-party harness adapter: an explicit trusted-project skill
  install/status/remove flow, an MCP stdio bridge, and a JSONL transcript importer. The tested
  Codex version set remains empty pending exact installed-artifact capability evidence. An import
  job belongs to the writer that published it: only that writer resumes it or replays its terminal
  report, and another writer submitting the same source bytes is refused rather than shown the
  owner's report, request id, or report locator.
- Backup, restore, and forward-only migration support with frontier-pinned manifests and verified
  route switches; see [`docs/runbooks/`](docs/runbooks/) for the operator procedures.

- **Full-screen terminal interface** as the interactive entry point (ADR-017, amending ADR-013
  decisions 1–2 and ADR-012 decision 2 as ADR-013 left it). Bare `yoetz` and `yoetz menu` on a
  real terminal open one continuous surface — session header, transcript, composer, and a stack
  of temporary views — with first run folded in as its opening steps rather than a separate
  wizard pass. Slash commands `/status`, `/work`, `/check`, `/receipt`, `/connect`, `/privacy`,
  `/provider`, `/service`, `/doctor`, `/help`, `/quit` name existing operations in plain
  language. Readiness renders as fifteen independently falsifiable layers rather than one
  "connected" state; `✓` is reachable only from a layer the owning service reported as verified.

  The interface adds no authority: `yoetz/tui/runtime.py` is the sole bridge to application
  services and originates no decision. MCP registration keeps preview → digest-bound confirm →
  verify (now via `setup.apply_codex_integration`, which requires the caller to echo back the
  exact preview *and* policy digests it displayed and refuses as stale if either moved — stricter
  than `--accept`, which still activates no policy trust). A foreign MCP entry remains a terminal
  block with no force-replace path. Privacy widening renders the exact disclosure and then hands
  off to `yoetz privacy propose|decide`; the interface never widens policy itself. No secret ever
  enters the interface: credential entry suspends the full-screen app and hands the controlling
  terminal to the existing confidential ceremony.

  Non-interactive behavior is unchanged. The interface opens only when stdin and stdout are both
  TTYs, `TERM` is usable, no CI marker is set, and `YOETZ_TUI` is not `0`; pipes, redirects, CI,
  `--help`, `--json`, named subcommands, `yoetz mcp serve`, and the protocol fixtures keep their
  exact previous bytes, and a bare non-TTY `yoetz` still prints help. Installations without the
  rendering dependency fall back to the ADR-013 prompt-loop menu, which remains supported.

  Adds one pinned runtime dependency, `textual==8.2.8` (with `linkify-it-py`, `mdit-py-plugins`,
  `uc-micro-py`; all MIT, all on the reviewed-license allowlist).

- First-run setup wizard (ADR-012): bare `yoetz` on an interactive terminal with no completion
  marker launches `yoetz setup run` — Codex PATH discovery with an explicit choice when several
  installs exist, preview-and-confirm MCP registration (`codex mcp get` first; foreign entries
  preserved, never replaced; success verified by re-reading state), a service reachability check,
  and printed next steps for the privacy-setup and provider-credential ceremonies, which remain
  human-driven. `yoetz setup status` reports the same posture read-only; every non-TTY bare
  invocation still prints help.

- Interactive control menu (ADR-013): bare `yoetz` on an interactive terminal opens a
  navigable menu (first-run still gets the setup wizard once, then lands in the menu), and the
  `yoetz menu` command opens it explicitly. The menu shows a status overview (service
  reachability, vault mode, Codex MCP registration, first-run posture) and dispatches to the
  existing operations — setup wizard, harness MCP/skill integration, provider-credential
  ceremonies, privacy posture reads, and service unlock/lock/stop — with every preview/confirm
  gate and confidential ceremony unchanged. Non-TTY, piped, and CI invocations keep the
  historical help output byte-for-byte.

- First-party Codex **live observation and advice** as a required v0.1 capability (ADR-010
  amendment): dual-source ingest (hooks primary + selective session-stream reconciliation), local
  `ObservationPort` control (`yoetz observe status|grant|pause|resume|revoke|reconcile`), unified
  `yoetz hooks observe`, project-level observation consent via private workspace commitment,
  automatic session↔task attachment without depending on MCP `start`, descriptor-safe workspace
  inspection, approved-check runner, and deterministic `AdviceSnapshot` guidance (optional semantic
  review remains additive). Still exactly six MCP tools; observation is CLI/service control only.
  Sensitive evidence stays encrypted; no unencrypted transcript spool; `hook_observed` requires real
  observation evidence under active consent.

- TOML alternate settings surface and owner-declared OpenAI-compatible endpoints (ADR-014 /
  issue #2): `config.toml` may bind Official OpenAI (`openai-responses`) or
  `owner-declared-openai-responses` with constrained `[provider.owner_declared_endpoint].https_origin`
  (HTTPS host+optional port only; no secrets, headers, or free `base_url`). Wizard and menu collect
  the same nonsecret choice; `yoetz provider endpoint` writes it. Owner-declared data-use defaults
  to `unknown` (never inherits `assisted`). Privacy desired-state TOML via
  `yoetz privacy export-desired` / `apply-desired` classifies tighten vs widen and never silently
  widens egress. Credentials, vault unlock, MCP registration, and widening decide remain
  ceremony-only.

- `yoetz integrate <harness> mcp status|preview|install`: digest-bound, preview-gated MCP server
  registration as a first-class command, backed by the sibling `HarnessMcpPort` and Codex
  discovery/registration adapters.

- `closure_readiness` on every `status` success (`open_obligation_count`,
  `unresolved_finding_count`, `blocking_conditions`), so an agent can see what currently bounds a
  completion conclusion before spending a `check` or `receipt` rather than learning it afterwards
  from an insufficient receipt. Derived per request: it records nothing, creates no verdict or IDs,
  and never strengthens coverage. When the compact singleton is unreadable both counts are `null`
  and the only condition is `readiness_unknown` — unknown is reported as unknown, never as zero.

- A worked `publish_work` example per ordinary publishable event family, so agents no longer
  hand-derive action/result/evidence/claim shapes from a large `oneOf`. `check`, `respond`, and
  `receipt` carry worked examples too.

- Publish-ready npm launcher at `support/npm-launcher/` for a future `npx yoetz`: a
  dependency-free delegator to the exact pinned `uvx yoetz==<version>`, kept deliberately
  unpublished (`"private": true`) until a separate release decision. It propagates signal
  termination as the conventional `128+n` exit code and gives actionable, platform-specific
  guidance when `uv` is absent; it installs nothing, bundles nothing, and duplicates no setup or
  interface logic.

- Public protocol documentation under [`docs/protocol/`](docs/protocol/) and the evidence-bound
  claim map at [`docs/public-claims.json`](docs/public-claims.json).

- **The registered MCP route is now observable.** Both Yoetz-owned Codex serve commands classify as
  `yoetz_owned`, so registration state alone reported a strict registration and a policy
  registration identically — and `yoetz provider status` never looked at the route at all, letting
  `semantic_ready: true` be read as "semantic review will run" on an agent route that cannot
  dispatch it. `yoetz provider status` now also reports `mcp_route` (registered profile, configured
  profile, and whether it was read) and a second, narrower `agent_route_semantic_ready` verdict;
  `yoetz integrate codex mcp status` reports `route_profile`, and `yoetz setup status` rows carry
  `registered_route_profile`. `semantic_ready` keeps its existing installation-local meaning and
  the exit code is unchanged: a strict route is a process-local ceiling (ADR-018), so CLI and
  terminal checks still dispatch, and it is reported as a blocker scoped to the agent route only.
  Route observation is fail-soft and never changes the exit code. A new
  [semantic dogfood runbook](docs/runbooks/semantic-dogfood.md) turns this into a preflight that
  declares which claim a run may make, and a provenance gate that refuses to score semantic quality
  when no provider attempt happened.

- **Influence dogfood protocol (docs/test-only).** A new
  [influence dogfood runbook](docs/runbooks/influence-dogfood.md) measures Yoetz-to-agent intervention
  without conflating operational health, authoring UX, semantic quality, and work-product influence.
  Offline unit tests lock classification rules (activation vs registration, strict-route Stream C as
  `not_tested`, early publication gate, miss taxonomy, honesty vs work-product influence, forbidden
  summary). No protocol, privacy, storage, or MCP surface change.

### Documentation and repository

- User documentation: `docs/architecture.md` (topology, module map, honesty rules), `docs/usage/`
  (install and first run, the terminal interface, the six operations, privacy and semantic review,
  providers and credentials, receipts and coverage), and `docs/README.md` / `docs/adr/README.md`
  indexes. `README.md`, `CONTRIBUTING.md`, and `AGENTS.md` were rewritten — the README had still
  described the repository as containing no implementation.
- Contribution intake: issue-first process with duplicate search, design gates for
  protocol/privacy/storage/release/ADR work, mandatory PR checklist, and required disposition of
  human and code-review-agent comments (`CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/`,
  `.github/pull_request_template.md`), plus root `AGENTS.md`, root `CODEOWNERS` for trust
  boundaries, and a SECURITY threat-model / out-of-scope table.
- **Retired the spec-mirror tree.** Yoetz was built spec-first, with one Markdown owner per planned
  file at a mirrored path under `specs/` (640 files, ~63k lines) plus a CI-enforced ownership
  manifest. That method finished its job — all 626 declared files exist — so the tree was removed
  rather than maintained as a second copy of shipped code. The authority chain is now `docs/adr/` →
  `docs/INTERFACES.md` → code and tests. `INTERFACES.md` and `OPEN_QUESTIONS.md` moved to `docs/`;
  `scripts/verify_spec_manifest.py` and its CI gates are gone; required check names are unchanged.
  The full tree stays recoverable at tag `specs-tree-final` (`git show specs-tree-final:specs/…`).
- Release and PR CI no longer swallow packaging/subprocess/integration suite failures behind a
  "tests not yet present" warning; those gates fail honestly.

### Hardened before first release

These defects were found and fixed during pre-release development. No published version ever
carried them; they are listed because each one describes the behavior that now ships.

- **Workspace inspection no longer follows mutable names after validation (security).** Opening an
  inspect workspace retains an authenticated root directory descriptor for the handle's lifetime.
  Later reads open descendants descriptor-relatively with no-follow semantics and re-check the root
  identity; replacing the root directory between open and inspect can no longer redirect a read
  outside the consented workspace. Intermediate directory symlinks that escape the root are refused
  (`symlink_escape`); in-root symlink chains remain readable up to a bounded depth. Content that
  changes during a single read is refused (`read_failed`) rather than returned.

- **Publish recovery must not mask a known authoring error.** When MCP `publish_work` body
  validation fails, envelope-first operation lookup still runs so a committed same-`request_id`
  operation can be recovered. A failed recovery read (for example nested `read_projection_failed`)
  no longer replaces the field-pointed `INVALID_REQUEST` with a false “no durable state changed /
  use a new request_id” message. Lookup is a closed tri-state: found recovery results retain
  precedence; authoritative absence returns the original authoring pointer; unavailable recovery
  returns retryable `OPERATION_PENDING` / `operation_recovery_unavailable` with the original
  publish `request_id` and the safe field that will apply once recovery can answer.

- **`check` can return a finding.** A check that raised even one finding committed durably and then
  failed to project, so the caller received `INTERNAL_ERROR` / `response_projection_failed` and
  never learned the verdict, the finding, or the semantic outcome — in every mode. The public
  result models are strict, and the internal result carried each nested entry in an immutable
  mapping type strict validation does not admit; the check result's projected finding additionally
  requires `provenance` to be present as an explicit null on a deterministic finding. Nested
  mappings are now normalized structurally at the one projection boundary — no coercion, no
  reordering, no defaults, and a genuinely invalid shape is still rejected at the same field.

- **An accepted durable `publish_work` is never reported as a failure.** When full response
  projection fails after the append succeeded, the daemon returns a reduced total-acceptance
  success (`ok: true`, `response_completeness: "accepted_projection_unavailable"`) with frontiers,
  accepted event ids/digests/sequences, and a `correlation_id` — not `INTERNAL_ERROR` /
  `response_projection_failed`. The agent needs no second `status` call and no same-`request_id`
  replay to learn what landed. Unexpected exceptions also write a durable owner-only diagnostic
  ring under `log_dir()` (`yoetz service diagnostics --correlation-id err_…`).

- **A post-commit projection failure now says where the write landed.** For non-publish writes (and
  the impossible minimal-envelope path), `response_projection_failed` still carries the committed
  `sequence`, `head_digest`, and accepted event `count` in `safe_details` / `accepted_state`.

- **Invalid tool arguments name what is admitted.** The response listed field locations only; it
  now also names the admitted enum members and the required identifier pattern for the rejected
  top-level fields, and points at the tool's worked example — all from checked-in schema bytes.

- **A receipt explains why a check does or does not count.** `check_not_applicable` appearing right
  after a successful externally-reviewed check read as a contradiction. The limitations section now
  states that the recorded check tested a different subject frontier, that its verdict stands only
  for what it tested, and that re-running `check` at this frontier is what makes it contribute.
  `check_not_recorded` and `check_payload_unavailable` are explained the same way.

- **A read is no longer told to replay a write.** A projection failure on `status` (or a privacy
  receipt read) advertised the same-`request_id` replay remedy as an accepted write, but a read
  appends nothing, so no operation record exists to replay against and the caller waited on a
  recovery that could not arrive. Reads now surface the retryable `read_projection_failed`, whose
  remedy is repeating the request. Observed in the 2026-07-27 Codex dogfood, where a compact
  `status` call failed twice and its exact replay failed again.

- **The post-commit projection window fails with bounded, named errors.** Rewriting a blocked leaf
  raised a bare `KeyError` when the omission pointer did not resolve in the body — reachable via
  the synthetic `/leaf-N` pointer produced when an origin pointer exceeds 256 bytes — and a bare
  `ValueError`/`IndexError` for a malformed or out-of-range array segment. These now raise
  `projection_pointer_unresolved`/`projection_pointer_invalid`. They are deliberately not remapped
  to `privacy_projection_blocked`: no policy blocked them, and that reason is non-retryable, so it
  would describe an already-durable append as a refusal. The projection stops before a response
  exists either way, so blocked content is never disclosed.

- **Installed guidance no longer points at files that were never packaged.** `skills/codex/yoetz/SKILL.md`
  linked four `references/*.md` paths absent from both the repository skill directory and the
  packaged resources; it now names the `yoetz://guidance/*.md` URIs the server already serves. A
  packaging test resolves every reference the installed skill names, reading only the packaged
  tree.

- **An accepted write is never reported as an unqualified failure.** A handler returning is the
  commit boundary; response shaping happens after it. An unexpected failure in that window now
  surfaces as the retryable `response_projection_failed` naming same-`request_id` replay, instead
  of a generic non-retryable `INTERNAL_ERROR` that both misdescribed the ledger and steered callers
  away from the idempotent replay that recovers it (ADR-008). Deliberate bounded failures raised in
  the same window pass through unchanged.

- Validation failures inside `expected_frontier`/`at_frontier` now name the offending leaf
  (`head_digest`, `sequence`) instead of projecting to the parent object, which reported only that
  something in the frontier was wrong. Caller-supplied extra keys are still never echoed.

- `EVENT_INVALID` now locates the rejected draft by ordinal and owning field (for example
  `/event_drafts/2/schema`), so a multi-draft batch no longer has to be re-derived to find the one
  bad member. The pointer is built only from frozen schema names and a bounded index.

- `yoetz provider status` now states which lifecycle it probed (`user_service_no_autostart`) and
  whether MCP-local composition starts on demand, so an absent service no longer reads as
  contradicting a working MCP session.

### Known limitations

- Independent security review of the vault, key hierarchy, and privacy gateway is a release gate
  that has not yet completed — see `docs/public-claims.json` for exactly which claims currently have
  evidence.
- v0.1 ships no production transport for the four non-LLM egress channels (telemetry, crash
  diagnostics, update checks, capability testing); they exist only as denied policy vocabulary.
- Native `launchd`/`systemd-user` service installation and headless passphrase unlock are not
  included; see [`docs/protocol/local-service-security.md`](docs/protocol/local-service-security.md).
- The advertised platform matrix is macOS 11.0+ arm64 and glibc 2.28+ x86-64 only; other platforms
  are untested.

### Fixed

- A provider credential was stored without ever being tried, so a wrong or expired API key looked
  identical to a working one until a check failed much later with a reason that could not name
  authentication. Setting a credential now dispatches one minimal authenticated request — a fixed
  literal body with a one-token ceiling, no task content — through the same hardened one-attempt
  transport a real review uses. Only the provider refusing the credential (401/403) withdraws it
  and fails the ceremony with `secret_rejected`; an authenticated rate limit counts as working,
  and a timeout, unreachable host, outage, or unrecognized model keeps the credential, because
  none of those establish that the key is wrong.
- A confidential ceremony expired after 60 seconds, which is a keystroke timeout rather than a
  human one: provisioning a provider credential means leaving the terminal for the provider
  console to mint an API key, and a minute did not cover it. The single
  `CEREMONY_EXPIRY_SECONDS` binding/challenge expiry is now five minutes. Foreground-terminal
  presence, the one-shot challenge, and the service/vault generation binding are unchanged.
- First run's questions could only be answered or abandoned. `b` now goes back one question from
  the review-mode and project-trust steps, distinct from `Esc`, which still cancels and changes
  nothing. Back is offered only on questions: it is inert on the integration approval, which
  applies a change, and a searchable picker still treats `b` as a query character.
- Semantic review reported `transport_unavailable` for every unavailable provider class except
  rate-limit and quota, so a rejected API key, a forbidden binding, a provider outage, and a real
  socket failure were indistinguishable on every owner-facing surface. The exact
  `SemanticFailureClass` is recorded as a durable owner-only diagnostic when present;
  `unclassified` is recorded when no class is available. The public taxonomy is unchanged and the
  record carries no provider-controlled text.
- The terminal interface previewed the Codex integration on one MCP route and applied it on
  another, so on a fresh installation the approved preview digest never matched and first run's
  Codex connection failed as `preview_stale`. The route now travels on the approved plan, and the
  approval screen shows the exact serve command that route registers rather than a fixed string.
- Abandoning the provider step during first-run semantic setup left the policy route registered
  with no provider behind it and no setup marker written. It now offers a local-only finish that
  re-registers the strict route through the same preview and approval.
- Changing privacy posture after setup never looked at the Codex registration, so moving to
  assisted review with an older strict registration in place produced a correct policy and a Codex
  session where every check returned `blocked_by_policy` / `route_semantic_ceiling` with nothing
  connecting the two. `yoetz privacy setup` now names the mismatch and the command that fixes it,
  and the terminal interface reports the agent-route verdict as its own readiness line.

### Security

- Losing the session-event monitor no longer auto-re-opens the vault on the next ordinary call.
  Idle, session-lock, and suspend locks describe conditions the service can watch recover; monitor
  loss removes the capability that produces those events for the life of the process, so
  auto-re-ready made that lock momentary and left the service running with session-lock relock
  silently no longer applying. A monitor-loss lock now holds until a trusted unlock ceremony.
- This is the first public release; see [`SECURITY.md`](SECURITY.md) for how to report a
  vulnerability. There is no prior version to carry a security fix forward from.

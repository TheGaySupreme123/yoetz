# Changelog

All notable user-visible changes to Yoetz are documented in this file. Format is a lightweight,
project-native heading style that marks the pending version as unreleased above
reverse-chronological released versions.

## Unreleased

### Fixed

- The native Cursor plugin's plugin-owned `mcp.json` now launches the exact Yoetz executable the
  plugin's hooks bind (the `/2` marker launcher) instead of a bare `yoetz` that Cursor's sanitized
  desktop PATH could resolve to an older ambient installation. `yoetz integrate cursor plugin
  status` adds a `launcher` section (executable `matched|drifted|missing|unbound|unobserved`,
  installed `mcp_binding`, and a bounded identity probed from that launcher's `version --json`)
  and `mcp.runtime.executable_activation`, which forces `full_restart_required` when a live Cursor
  helper child runs a different executable. Trees rendered before this fix stay marker-valid and
  report `modified` / `mcp_binding: ambient_path` until one exact previewed replace (issue #468).

- The cooperative MCP bridge latches the first availability failure of its host binding
  (`service_unavailable`, `service_incompatible`, `protocol_mismatch`, `endpoint_unsafe`,
  `peer_untrusted`). Later calls under a new `request_id` — including delegated workers sharing
  the MCP process — inherit the same public error and `correlation_id` with
  `safe_details.availability: terminal_unavailable` and `availability_inherited: true`, and mint no
  new diagnostic, spawn, or supersede. The original `request_id` replay, a changed service holder,
  or one quiet successful handshake clears it. Agent guidance now carries a bounded
  `yoetz_availability` block into delegated assignments and forbids lifecycle commands from
  `INTERNAL_ERROR` (issue #469).

- Codex activation recommendation decisions now bind the exact executable path/bytes/version,
  canonical home, activation preview, and rendered-cache digest. Another Codex home or a target
  that drifts back to `installed_not_activated` receives fresh actionable advice; active targets
  stay quiet, declines remain exact-target-only, legacy unscoped decisions suppress nothing, and
  activation still requires a freshly shown digest and confirmation (issue #463).

- Disposable-worktree Codex dogfood now has an executable parity gate that fails before launch on
  missing exact-worktree consent or activation and retains packaging, discovery, host delivery,
  model use, hooks, mapping, drain, session stream, semantic provenance, receipt, influence,
  rollback, and normal-target isolation as separate closed-state cells (issue #464).

- The MCP stdio bridge no longer exits on a size-valid but excessively nested JSON frame.
  Inbound nesting is bounded at the canonical codec's 64 levels with a non-recursive walk, any
  decoder `RecursionError` is converted to the fixed `invalid_json` parse error, and a valid
  request succeeds immediately after a rejected deeply nested one (issue #394).

- `yoetz integrate <host> plugin --help` no longer advertises lifecycle commands a host cannot
  run. Each command's help names its hosts (Codex: `preview`, `status`, `remove`; Cursor adds
  `install`; Claude Code has the full lifecycle plus `export`), and invoking an unsupported command
  refuses before any binary discovery or mutation with
  `<host>_plugin_command_unsupported:<command> supported=...` instead of the bare
  `codex_plugin_command_invalid` (issue #465).

- Consented `SessionStart` hooks on Claude Code, Codex, and Cursor now auto-attach a ledger task:
  the shared hook auto-start sends the paired `workspace_ref` (canonical workspace root) and
  `external_ref` (host session) selector the `start` contract requires, validated through the
  public request model before dispatch. Previously the request was always invalid and the failure
  was swallowed, so natural auto-attachment never happened and `observe status` showed no mapping
  without a cause. Every failed attempt now records a closed payload-free `hook_diagnostics`
  reason (`auto_attach_workspace_unbound`, `auto_attach_request_invalid`, `auto_attach_conflict`,
  `auto_attach_refused`, `auto_attach_result_invalid`, `auto_attach_mapping_write_failed`,
  `privacy_authority_required`, or the shared service/vault/timeout/storage tokens) (issue #459).

- Cooperative MCP calls no longer collapse typed local-control failures into opaque,
  non-retryable `INTERNAL_ERROR`. Service absence, an accepted-but-unresponsive listener,
  incompatible or mismatched installations, unsafe or untrusted endpoints, timeouts, and bounded
  protocol refusals now retain an actionable public code, truthful retryability, a structural
  `reason_code`, resolvable correlation diagnostics, and same-`request_id` recovery guidance;
  genuinely unexpected bridge defects remain `INTERNAL_ERROR` (issue #460).

- `yoetz observe status` no longer collapses bounded observation-store filesystem failures into
  generic `internal_error`. Unsafe state/lock shapes, unavailable open/permission/read-only/
  missing-parent/lock failures, and corrupt stored data retain distinct typed outcomes; fixed
  remediation omits the raw absolute state path, and unexpected non-filesystem defects still reach
  the internal-error boundary (issue #428).

- Reopening a task whose one pending check has an undecodable resume checkpoint no longer latches
  non-retryable `STORAGE_CORRUPT` for the entire ledger. Recovery quarantines that operation as
  `operation_resume_object_invalid`, stores the terminal error for same-request replay, and leaves
  events, projection, writers, and every other operation readable and writable. A corrupt event
  chain or object inventory row still fails the bundle once; an environmental `OSError` during
  rehydration still retries without latching (issue #443).

- A check parked on a standing repository-grant handoff no longer refuses every observation
  append for the rest of the session. `suspension_kind=repository_grant` expires the lease
  and is not an active frozen-case barrier, so the drain can ledger hook-observed work
  while the trusted ceremony is outstanding. Same-request replay re-installs the barrier
  and still commits if only observation moved past the frozen subject frontier; cooperative
  motion still conflicts (issue #445).

- A tracked root `CLAUDE.md` whose complete bytes are the reviewed `@AGENTS.md` alias no longer
  makes every later pull request fail the source publication-boundary gate. The exception is
  source-only and exact; nested aliases, changed content, and packaged copies remain blocked
  (issue #455).

- Codex skill install, replace, and remove now refuse a symlink at `.agents` or `.agents/skills`
  as `target_unsafe`, bind the managed parent's identity into the preview digest, and perform the
  stage/replace/remove swap through a directory-fd no-follow walk so a parent swapped between
  preview and apply fails closed (issue #396).
- `inspect_activation` / `yoetz observe status` report `installed_not_activated` for a byte-present
  modified or untrusted plugin tree instead of collapsing it to `not_installed`. `not_installed`
  remains absence only, and a modified tree is never `active`. Apply still requires a current
  renderer variant (issues #347 and #387).

- Replaying a check that was suspended on a standing repository grant no longer fails with a
  non-retryable `STORAGE_CORRUPT` after the trusted `yoetz --privacy` ceremony, and a task whose
  ledger holds `evidence_recorded/1.1.0` events (any evidence carrying a `digest_binding`) no
  longer becomes unreadable the moment a pending check has to be rehydrated from its durable resume
  checkpoint. The checkpoint's projection snapshot records no schema version and its decoder pinned
  every event family to `1.0.0`, so a digest-bound evidence record encoded fine but could never be
  decoded again; every deferred rehydration — the same-request replay after the grant, and ledger
  recovery after a service re-ready while the check was pending — was reported as corruption of an
  intact bundle. Snapshot records now decode under the newest wire version their family admits
  (issue #427).
- Cursor plugin status no longer treats installed `mcp.json` bytes as live MCP runtime. After a
  plugin-managed replace, a surviving shared Cursor helper child on `yoetz mcp serve --semantic off`
  is reported as `mcp.runtime.activation=full_restart_required` instead of looking activated, and
  agent guidance treats `route_semantic_ceiling` against an installed `policy` route as that
  mismatch rather than an owner privacy decision. Reload Window is not a sufficient activation
  instruction; recovery never authorizes egress (issue #426).

## 0.1.0 — Public alpha (2026-08-20)

Initial public alpha release. The earlier 0.0.1 registry packages only reserved the project name
and contained no usable Yoetz implementation.

### Added

- A `status` item's `freshness` scalar no longer contradicts the `coverage.ledger_freshness` in the
  same item. A check that declared a coverage gap records `partial` freshness, but the projection
  reported that only while folding the check itself and reverted to `current` on the next event of
  any family — a receipt or a re-attach was enough — while the item's coverage kept the gaps. The
  projection scalar is now derived from the retained check rather than from whichever event is being
  folded, so it holds `partial` for as long as that check governs, and the compact item reports the
  weaker of the scalar and its own coverage the way the evidence view already did. An agent reading
  the summary line is no longer told the ledger is clean while the structured coverage records the
  gaps (issue #307).

- `provenance_disputed` is a fourth `respond` disposition. It records that the responder contests
  the finding's authorship or provenance premise rather than its conclusion, requires a reason, may
  carry evidence, and is not scored as an evidence-free rejection by either deterministic policy
  pack. Like every other disposition it never resolves or erases the finding. The MCP `respond`
  surface advertises it in both the tool description and the `disposition` field rules, so a caller
  reading only the advertised schema learns the rule (issue #224).

- Status compact/readiness projections now distinguish `unanswered_finding_count` from
  `receipt_blocking_finding_count`. Responses clear the former; current actionable receipt findings
  remain in the latter for every disposition. The paired `findings_unanswered` and
  `receipt_findings_unresolved` conditions tell agents whether to respond or proceed to an honestly
  unresolved receipt, and agent guidance no longer offers the human-only `waived` disposition
  (issues #286 and #287).

- The idle relock clock now counts harness observation rows resolved by the ready sweep as
  activity, so a live workspace whose hooks keep delivering events is never relocked underneath
  an open task session — however long the run — while a workspace that truly goes quiet still
  relocks one full window after its spool runs dry. The default idle-relock interval is now 3600
  seconds (was 900, shorter than one legitimate implementation phase under the prescribed publish
  cadence), and the process-idle stop is now 7200 seconds so the in-process soft relock — which
  re-readies on the next ordinary call — always comes before the full process stop (issue #291).

- MCP success summaries now preserve the canonical frontier head digest and, for generic
  operations such as `start`, returned task/session/writer identifiers after strict shape
  validation. The bounded text fallback can therefore seed the next request even when a host drops
  `structuredContent` (issue #279).

- Local-only SessionStart observation now emits the static attach advisory without opening a
  service connection, and pending standing advice shares that bootstrap context instead of being
  starved (issue #280).

- Observation sweeps that raise now retain the exception's bounded reason and origin in owner-only
  diagnostics; only the actual sweep deadline emits `sweep_deadline_exceeded` (issue #278).

- `publish_work` rejections for a payload field placed on the wrong event family now name the
  field's one legal owning family (issue #266). When an `extra_forbidden` key byte-equals a frozen
  catalogued payload property with exactly one owner among the ordinary publish families — the
  2026-08-14 dogfood case was `attempted_items` on `claim_recorded`, owned solely by
  `action_recorded` — the error carries a flat `repair_*` fact in `safe_details`, one bounded
  ownership sentence in the authoring hint, and a `Repair:` clause on the compatible text summary.
  The request stays rejected, nothing is moved or reinterpreted, caller-invented keys are never
  echoed, and ambiguous or envelope-owned fields keep the plain admitted-key answer from issue
  #240. Agent-facing guidance now teaches the same ownership before the first call (issue #264):
  the skill, workflow, and agent instructions name `action_recorded` as the sole owner of
  `attempted_items`; the request templates show the `requested_items` → `attempted_items`
  exact-value pairing, call `decision_recorded.authority` a structural actor id, and name the
  closed `action_kind` enum with `edit` for source changes; and the model-visible `publish_work`
  description front-loads all three rules for hosts that degrade schema metadata.

- Cooperative writers now receive a one-shot, coalesced notice when the observation writer moves the
  task frontier, including the bounded sequence range and observation-record count. The notice map
  is capped, drops ended-session entries, and is reconstructed on retry from a completed append's
  frontier metadata when the local write did not land. A per-session delivered high-water
  `to_sequence`, scoped to the announced task ledger, survives notice deletion so a replayed
  append is not re-announced. Successful
  routine reads remain available in the local observation store but are rate-limited out of the
  task ledger; failures, denials, path-qualified executables, and conservatively unrecognized
  commands still materialize normally (issues #244 and #322, ADR-022 amendment).

- Consent-based Codex observation activation and durable delivery repair (issues #204 and #205,
  ADR-012 amendment): setup now distinguishes installed hook sources from an active plugin, previews
  the exact selected Codex executable and explicitly selected existing home, repository marketplace,
  append-only plugin-enable change, post-consent scoped inventory/add commands, and versioned
  byte-exact cache transition, and applies them only after digest-bound approval. Its pre-consent
  version probe runs in a disposable owner-private home; selected-home inventory and scratch effects
  begin only after approval with both Codex home variables forced to the approved target. `active`
  requires installed managed sources, exact
  marketplace/config state, Codex's installed-and-enabled inventory row for this repository, and a
  cache identical to the managed plugin tree; marketplace/config bytes alone remain
  `installed_not_activated`. A post-write failure preserves approved partial state for an honest
  retry; it never risks deleting or overwriting concurrent state through pathname rollback. Setup
  probes use isolated state instead of polluting the live outbox.
  Observation delivery no longer depends on an already-existing lifecycle mapping: READY and
  periodic service sweeps recover pending envelopes, retry metadata and bounded hook diagnostics
  explain failures, and `yoetz observe status|drain` reports and repairs undelivered work without
  silently deleting it. `[observation].enabled` is a typed local configuration gate that defaults
  on; per-workspace observation consent remains independently required.
  Observation drains preserve FIFO within each session lane, retire a lane after its head fails,
  quarantine ended unmapped sessions only after fencing against a concurrent attach, and stop a
  service sweep after one workspace-global rejection. Turn-boundary auto-attach retries are
  bounded and diagnostically visible. Transient
  ready-application activation failures after a successful soft unlock remain retryable for three
  attempts, and MCP now preserves the daemon's retryability in `VAULT_LOCKED` guidance.
  Observation hooks no longer block the session (#209/#210/#211): pure-ingress handlers are
  declared `"async": true`, advice-returning handlers get a meetable 10-second budget, the control
  handshake hashes the schema manifest without building the catalog, the local store caches its
  parse behind a stat validator, and the drain stops re-probing sessions whose rejections cannot
  heal mid-pass. Quarantined observation detail is now bounded by a clock-fenced 14-day age in
  addition to count and byte caps, `yoetz observe reclaim --workspace <path>` drops it explicitly,
  and `yoetz observe status` reports quarantine depth plus separate involuntary-eviction and
  operator-reclaim counts.
  **Upgrade note:** the hook timeout/async changes alter Codex's per-hook trust hashes. On an
  existing install every changed hook reports `Modified` and is silently skipped until re-approved
  through Codex's startup hooks review (or the equivalent trust configuration); until then only
  the unchanged hooks fire. Re-approve the yoetz hooks after upgrading the plugin tree.

- A reusable recommended-defaults advisory surface (ADR-021): releases register reviewed
  recommendations, heavy control points cache pending items, and SessionStart may ask the agent to
  explain at most one item and request user approval. `yoetz recommend list|accept|decline` is the
  only application/decision path; declines are remembered and never re-nagged, and upgrades never
  flip configuration or Codex activation silently. Initial consumers cover observation enablement,
  Codex plugin activation, and the existing policy-gated PyPI update advisory. The durable
  `update_checks` channel remains the only update-check flag, accepts only bounded package identity,
  and never auto-upgrades; Yoetz has no npm update check because the npm package delegates to the
  canonical exact-version PyPI distribution.

- Typed evidence digest provenance (`evidence_recorded/1.1.0`) records the exact closed byte
  subject, availability, byte count, and authority that established each new digest. Kind/subject
  contradictions fail closed; approved checks publish bounded service-owned receipts; legacy,
  digest-only, withheld, redacted, and semantically omitted evidence remain explicit limitations
  without changing frozen `evidence_recorded/1.0.0` bytes (ADR-020). In semantic review, a
  digest-bound evidence record with a bounded caller `description` surfaces that description as
  the excerpt text while the digest identity travels alongside on the excerpt ref
  (`digest_provenance`), so honest provenance no longer hides the content from the reviewer
  (issue #176).

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
  `ObservationPort` control (`yoetz observe status|grant|pause|resume|revoke|reconcile|drain|reclaim`), unified
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
  finding counters, `blocking_conditions`), so an agent can see what currently bounds a
  completion conclusion before spending a `check` or `receipt` rather than learning it afterwards
  from an insufficient receipt. Derived per request: it records nothing, creates no verdict or IDs,
  and never strengthens coverage. When the compact singleton is unreadable all counts are `null`
  and the only condition is `readiness_unknown` — unknown is reported as unknown, never as zero.

- A worked `publish_work` example per ordinary publishable event family, so agents no longer
  hand-derive action/result/evidence/claim shapes from a large `oneOf`. `check`, `respond`, and
  `receipt` carry worked examples too.

- Public npm launcher at `support/npm-launcher/` for `npx yoetz`: a dependency-free delegator to
  the exact pinned `uvx yoetz==<version>`, published only after the matching Python artifact by the
  protected tagged workflow. It propagates signal
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

- Agent-start guide: `docs/usage/agent-start.md` addresses the coding agent installing Yoetz on a
  user's behalf — why setup's questions never appear in an agent shell (TTY-gated by design), the
  exact decision list to relay to the human, the post-setup provider-credential recommendation,
  the scriptable surfaces an agent may use with explicit user instruction, and the prohibitions
  (no credential handling outside the warned consent lane, no privacy widening, no foreign-entry
  overwrite). Linked from
  `README.md` and `docs/usage/install-and-first-run.md`; fetchable raw until `yoetz.dev` hosts it.
  The two surfaces an agent actually lands on now point at it: the non-TTY bare `yoetz` help
  footer, and a `next_steps` entry in the `yoetz setup run` non-interactive dry-run report.
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
  keeps the field-pointed, non-retryable `INVALID_REQUEST` primary and carries
  `operation_recovery_unavailable` as a `reason_code` beside the `fields`/`reasons` locations in
  the same `safe_details`, with a caveat that a prior operation under this `request_id` could not
  be checked and that resubmitting the corrected body lets the service replay or reject the
  `request_id` itself. When nothing is locatable, `safe_details` carries that `reason_code`
  alone.

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

- **Answering a finding no longer discards the check that raised it.** `response_recorded` is a
  material family, so the guidance-mandated `check` → `respond` → `receipt` sequence always ended
  in `check_not_applicable`: a successful semantic check's
  `check_types: ["deterministic", "semantic_model_derived"]` never reached the receipt, which
  claimed only the `["deterministic"]` baseline it would have carried with no check at all. A
  response to a finding the applicable check itself returned now leaves that check attributable —
  its coverage and semantic gaps fold into the receipt — and the receipt declares the new gap
  `check_current_as_of_earlier_frontier`, stating that the verdict is current as of the subject
  frontier it tested rather than the receipt's. Responses to findings the check did not return,
  responses whose payloads are redacted or unreadable (they cannot prove which finding they
  answered), and every other material event still require a re-check. Compact status uses the same predicate, so
  status and a receipt at one frontier cannot disagree (issue #172).

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
- v0.1 ships no production transport for telemetry, crash diagnostics, or capability testing;
  they exist only as denied policy vocabulary. Structural package update checks are the sole
  non-LLM exception: when independently enabled, they use the bounded allowlisted PyPI transport
  and never carry task/user content or upgrade the package automatically.
- Native `launchd`/`systemd-user` service installation and headless passphrase unlock are not
  included; see [`docs/protocol/local-service-security.md`](docs/protocol/local-service-security.md).
- The advertised platform matrix is macOS 11.0+ arm64 and glibc 2.28+ x86-64 only; other platforms
  are untested.

### Fixed

- Hook observation advice no longer tells an agent `Next: refresh_observation`. That token
  remains the snapshot next-action value, but the hook clause now names the real host-shell
  step (`yoetz observe status`) and asks the agent to disclose the gap if it persists at
  check time. `guidance/workflow.md` lists all ten next-action tokens as English next-move
  names, not tools (issue #323).

- The request-template fallback now names the closed `requested_items[].item_kind` enum
  (`change`, `command`, `file`, `source`, `url`) and shows a `change` example beside the
  existing `command` entry. Guidance's own "requested outcome" vocabulary is confined to
  `description` / `acceptance_criteria`, so `outcome` is no longer primed as a kind (issue #318).

- A finding-wording change no longer wedges an in-flight check on its own `request_id`. The
  persisted deterministic-result checkpoint requires byte-equal rendered finding text on replay,
  so any wording edit made every pre-change checkpoint replay as non-retryable `STORAGE_CORRUPT`
  ("The deterministic checkpoint is corrupt.") while the continuation kept pointing at the same
  request. The checkpoint now stamps the kernel's rendered finding-text contract digest; on
  replay, a checkpoint whose bindings verify but whose stamp is absent (pre-change format) or
  from different wording is treated as superseded and the deterministic phase recomputes from the
  unchanged digest-verified frozen case in the same invocation. Genuinely broken checkpoint
  content still replays as `STORAGE_CORRUPT` (issue #340).

- Frontier-motion notices no longer re-announce already-delivered observation appends when the
  outbox redelivers a committed envelope. The local store keeps a per-session delivered high-water
  `to_sequence` after the hook consumer receives the notice; `note_frontier_motion` drops
  candidates at or behind that mark and clamps overlapping ranges so later genuine motion starts
  from the announced head and record counts do not double-count. The mark is scoped to the
  announced task ledger: when a session's mapping moves to a different task, the stale mark is
  discarded rather than silently suppressing the new ledger's motion (issue #322).

- Receipt replay of a completed `request_id` no longer collapses object-store faults into
  unclassified `INTERNAL_ERROR`. Verification mismatches (tampered or missing envelope, wrong key
  slot) are non-retryable `STORAGE_CORRUPT` with the stored-receipt-invalid family. Environmental
  I/O on a durable valid envelope is retryable `STORAGE_UNSAFE`. Pre-append `stage`/`finalize` I/O
  on a fresh receipt is also retryable `STORAGE_UNSAFE`. Classified receipt storage faults carry a
  resolvable `correlation_id` with a bounded exception-class reason and optional `yoetz` origin so
  the corruption runbook can join the public envelope to the diagnostic ring without an
  `internal_error` diagnostic (issues #325, #336, #337).

- The MCP initialize `instructions` string had no size bound and had grown to 41 KB by inlining
  three guidance documents. Codex copies that string into the `description` of every advertised
  tool, so it was charged seven times on every turn of every session — roughly 288 KB of advertised
  surface, six-sevenths of it duplicate, which a dogfood session hit as a truncation warning.
  `instructions` now carry `agent-instructions.md` alone; `workflow.md` and
  `coverage-and-receipts.md` are reached through the catalog paragraph that document already
  carries, and through the `read_guidance` tool that has served them since the same issue the
  inlining was working around. The packaged Codex skill no longer claims the two documents are
  already in context. `SERVER_INSTRUCTIONS_BUDGET` and `ADVERTISED_SURFACE_BUDGET` now bound the
  instructions block and the aggregate advertised surface, asserted alongside the per-schema
  budgets, so the next oversized guidance edit fails CI instead of a live session (issue #300).

- Correlated Codex `PreToolUse` and `PostToolUse` phases no longer claim one SQLite logical identity
  with incompatible operation digests. The canonical host-call identity remains the content and
  ledger-dedup key, while the durable claim key is additionally scoped by the materialization
  version and exact draft-role tuple; pre-action and paired-result phases therefore cannot collide,
  and hook/stream copies of the same paired phase still merge to `source_mask == 3`. Claims record
  the source-independent materialization version rather than the source cursor version. A genuine
  claim conflict now quarantines only that envelope as `dedup_conflict`; only bundle corruption arms
  the READY-generation session latch (issue #309).

- A Codex hook with a stale lifecycle mapping treated the daemon's `SESSION_CONFLICT` or
  `SESSION_NOT_FOUND` response as proof that the service was unavailable, then repeated that false
  advisory forever because it retained the old session and writer ids. Status errors now pass
  through an exhaustive classification: stale mappings tell the agent to call `start` again while
  explicitly preserving service health, transient reads request a later status read, privacy and
  vault conditions name their actual recovery paths, and only genuine degradation says the service
  is unavailable. The stale advisory remains advice-safe and the agent's successful `start` result
  is still the only source of a replacement mapping (issue #308).

- The end-to-end hook budget was smaller than the budgets enforced inside a single pass, so
  `hook_budget_exceeded` fired on healthy hooks — 253 of 833 diagnostics on one workspace — and
  carried no signal about the regressions it was added for. The total is now derived from the
  connect preflight and drain budgets plus a local-stage allowance rather than being an
  independent constant, and events that may retry auto-attach carry that enforced budget too. A
  test asserts the derivation so the parts can no longer drift past the whole (issue #288).

- `stream_partials` was the only unbounded collection in the observation state file and was
  absent from the eviction ladder, so two entries could hold 41% of a file at 95% of its hard
  1 MiB cap while overflow evicted envelopes and outbox rows around them. A partial is a
  read-cache — the reader rereads the tail from the committed cursor whenever it is missing — so
  it is now bounded per entry, dropped with an explicit gap instead of raising (which previously
  stalled the stream while retaining the partial that caused the stall), and shed first in the
  `_save` eviction ladder, ahead of any durable row. The per-entry bound is the reader's own read
  chunk and may not fall below it: the reader assembles a source line longer than one chunk by
  holding its prefix, so a smaller bound would drop that prefix every pass and freeze the cursor
  for that session. Oversized partials persisted before this
  change are dropped on the next save; the condition projects as `source_lag` on status until a
  reconcile catches up (issue #289).

- The `store` stage of every hook pass cost ~500 ms on a full state file and the diagnostic could
  not say where it went. Canonical JSON validation and escaping walked every string one character
  at a time in Python, which was ~70× the stdlib cost on the same bytes; both hot loops now take a
  single C-level scan and fall back to the original logic only when a string actually contains an
  escape or an invalid codepoint, so output and error identity are unchanged. Measured against a
  live 1 MiB state file, one parse, hydrate, encode, and write fell from ~275 ms to ~55 ms — parse
  ~82→13 ms and encode ~155→24 ms. Timing rows now also
  break `store` into `store_hydrate`, `store_encode`, and `store_write` so any remaining cost is
  attributable, and a latency fence bounds the codec against the stdlib on a realistically-sized
  document (issue #290).

- An evidence-free rejection or waiver of a current deterministic finding minted two actionable
  findings, `questionable_finding_rejection` and `weak_or_stale_response`, doubling the response
  burden at every level. `check` now collapses that overlap when it composes the built-in packs,
  keeping only `questionable_finding_rejection`. `weak_or_stale_response` still stands on its own
  for stale responses, for unsupported current responses to semantic findings, for stricter
  work-integrity-only evidence exclusions, and whenever the research-evidence pack did not run
  (issue #285).
- Codex Step 0 treated an empty MCP `resources/read` as success and advertised that guidance URIs
  "resolve without any repository checkout". The skill now stops on an empty body, calls
  `read_guidance` with the same URI, and opens the matching installed `references/<name>.md` copy
  if that tool result is also empty. MCP registers `read_guidance` as a seventh, read-only tool that returns the
  full guidance document as tool text and is not a ledger operation. `resources/read` now
  advertises each registry `media_type` instead of hardcoding `text/markdown`.
  `docs/INTERFACES.md` no longer states that unprofiled hosts can fetch those documents
  unconditionally (issue #203).
- Codex Step 0 never named `resources/list`, but agents still called `list_mcp_resources` first
  and treated `Unexpected response type` as a missing server. The skill and initialize
  `agent-instructions.md` now say not to list: the five `yoetz://guidance/` URIs are the
  complete catalog, and a list failure is not a reason to stop or to read product source
  (issue #173). The served list payload stays spec-correct. `rmcp 3.0.0` (Codex
  `0.148.0-alpha.6`'s pin) decodes that payload as `ListResourcesResult` for every
  optional-field subset, so this does not strip list fields and does not claim a host-side
  decode fix.
- A Stop hook that selected advice completed in ~1.4s with exit 0, then Codex marked it Failed
  with `hook returned invalid stop hook JSON output`. Stop has no `hookSpecificOutput`; the
  event-agnostic emitter was writing `additionalContext` onto a wire type that only admits
  universal fields plus `decision`/`reason`. Stop advice now uses `decision: block` plus
  `reason`, with `stop_hook_active` as the host loop guard. SessionEnd no longer peeks or
  commits advice: the host discards that stdout, so a delivery there would consume text the
  agent never sees (issue #222).
- The very first MCP call after a cold start could return `SERVICE_UNAVAILABLE` from a healthy
  install: the daemon published its control endpoint and accepted connections up to ~19 seconds
  before it could answer a handshake, and the on-demand connector treated the silent socket of
  the daemon it had itself just spawned as wedged, abandoning most of its 30-second budget after
  ~8 seconds. The endpoint is now published only after activation settles — a connect during
  startup is refused, which the connector correctly treats as still-starting — and the post-spawn
  poll loop keeps polling an accepted-but-silent successor to the deadline. A genuinely wedged
  pre-existing daemon still fails fast without spawning a successor (issue #235).
- `yoetz service run` against a live daemon holding the singleton lock reported
  `internal_error: the command could not be completed` (exit 70), telling one dogfood agent the
  service had died and could not be restarted while it was alive the whole time. The refusal now
  reports `service_already_running` with the holder pid (best-effort, stamped advisorily into the
  already-held lock file) at exit 20, every known `LifecycleError` reason maps to a truthful
  bounded message before the catch-all can degrade it, and `service status` distinguishes "no
  daemon — start one" from "a daemon is listening but not answering — do not run `service run`;
  it will refuse" (issue #237).
- The daemon could starve its own control plane for 10–20 minutes with zero diagnostics and then
  recover silently: the observation sweeper ran every store call — each a blocking cross-process
  flock plus a full workspace-document re-encode — synchronously on the event-loop thread, a
  corrupt pending row made every later RPC repeat the entire failed ledger recovery, and the
  sweeper task could run before the accept loop existed. Sweeper and coordinator store writes now
  run off-loop (making the 30-second sweep deadline enforceable for the first time), a failed
  recovery latches its verdict instead of replaying the ledger per call, the first sweep waits
  for the control accept loop to arm, and a watchdog thread that cannot be blocked by the loop
  reports `control_plane_saturation_entered`/`_persists`/`_cleared` with loop lag and in-flight
  counts to the diagnostics ring while the outage is happening (issue #238).
- A `publish_work` whose body failed bridge-local validation while the service was unreachable
  returned retryable `OPERATION_PENDING` prescribing a same-`request_id` retry that could never
  succeed, burying the deterministic `INVALID_REQUEST` the bridge already held — and a declared
  `dry_run: true`, which appends nothing, still paid the ~5-second recovery lookup. A dry-run
  validation failure now returns the field-pointed `INVALID_REQUEST` with no service round-trip;
  a real submission with the oracle unreachable keeps the validation result primary and states
  both known facts — the body failed locally, and the prior-operation check could not run —
  non-retryably, alongside `reason_code: operation_recovery_unavailable` (issue #239).
- Unknown payload keys on an event draft were reported as `invalid_type_or_value` at
  `/event_drafts/N/schema/name` — a field that was correct — with a hint reciting envelope
  requirements the request already satisfied, because the jsonschema oneOf scorer weighed
  discriminator failures from branches the caller never selected and the closed
  `extra_forbidden` token was destroyed on the jsonschema-to-pydantic boundary. The scorer now
  restricts itself to the branch the discriminator selected, `extra_forbidden` survives to
  `safe_details.reasons`, and the hint states what happened — "the payload carries N properties
  the `<family>` schema does not admit" — naming the admitted keys (frozen schema content) and a
  bounded count, never the caller-controlled key names. Frozen payload property names joined the
  safe-location allowlist with an import-time completeness gate, so a bad value under one now
  points at that key instead of collapsing to the payload root (issue #240).
- A standing `provider_not_ready` advice was injected 29 times byte-identical in one 24-minute
  session: the delivery gate keyed on a suppression identity that hashes the whole retained
  envelope stream, so it churned on every tool call while the rendered text never changed. Hook
  delivery is now deduplicated on a content identity over exactly what the agent receives —
  materialization, ledger history, and `observe status` keep the evidence-sensitive identity
  untouched, and the content identity excludes evidence references because some rules cite a
  rolling window over the envelope stream — and standing machine conditions the agent cannot act
  on (`connect_provider`)
  travel only on session-boundary events, falling through to the next actionable item on
  per-tool-call hooks rather than masking it (issue #241).
- Every workspace exec call paid 3.5–7.5 seconds of synchronous observe-hook overhead — process
  startup importing the full CLI graph (~325 ms per hook) and, dominating on a lived-in store,
  10–18 full serialize-and-fsync cycles of the workspace state file per hook. The hook entry now
  goes through a minimal shim with a lazy application package (~45 ms of imports), a hook pass
  batches its state mutations into single flushes fenced before any service RPC, an unchanged
  advice snapshot is no longer rewritten, and end-to-end hook timing is measured with a
  `hook_budget_exceeded` diagnostic. Measured end-to-end: ~0.55 s against a realistic store,
  from 1.7–2.5 s (issue #242).
- `yoetz state capture` could not capture Yoetz's own repository: the pre-hash safety walk
  counted every filesystem entry under the root — including the root `.git` object store and
  gitignore-excluded trees like a vendored `.venv` — against the 10,000-entry bound, 40× more
  than the population capture actually hashes, and the failure never said which bound tripped.
  The walk now skips the root `.git`'s internals (nested `.git` rejection and every per-entry
  safety check are preserved outside gitignore-excluded subtrees) and prunes fully-ignored
  subtrees using git's own exclusion
  semantics, and any file-count limit failure reports the bound that tripped with observed count
  and limit as integers in `limit_detail` (issue #243).
- A receipt reported `semantic_review_not_requested` for a task whose semantic review had been
  requested and refused. The stop-rules make a blocked review a coverage gap rather than a retry,
  so the agent falls back to `deterministic_only` — and that successor check replaced the recorded
  one wholesale, leaving a label that blamed the agent for never asking. A deterministic-only check
  now carries the earlier attempt's gap (`optional_semantic_review_blocked_by_policy`,
  `semantic_review_not_configured`, or `semantic_relevance_review_not_run`) forward alongside it,
  so the receipt still names the environment as the cause (issue #185).
- Prose that publishes cleanly could vanish from semantic review without a word. Publish accepts up
  to 8192 bytes per field while one case item carries 4096, so text in between was silently
  shortened — or, for a whole event payload, replaced by a bounded-omission marker whose `reason`
  read `not_selected`, the same token used for material the selection policy declined to send.
  The marker now says `over_case_item_limit`, and any case that had to shorten or replace admitted
  text raises `semantic_case_content_over_item_limit` in the check and receipt coverage (issue
  #177).
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
- Ordinary store-lock contention discarded hook events as `runtime_gate_unsafe`: the runtime-gate
  read serialized on the interprocess store lock, whose two-second acquisition timeout is routinely
  exceeded by a concurrent hook's batched local pass, and the guard treated the resulting
  `TimeoutError` as an unsafe gate — dropping the event before capture with no stderr line and no
  workspace gap, on a workspace every health surface reported as covered. The gate is now read
  lock-free through a single descriptor (it is an owner-only marker replaced only atomically), a
  contended read is reported as `runtime_gate_contended` and falls back to the missing-marker
  default instead of discarding the event, and a genuinely unsafe gate still fails closed but says
  so on stderr and records an `observation_storage_corrupt` coverage gap for the bound workspace so
  `observe status` can see the drop (issue #273).

- `hook_diagnostics` in `observe status` was an all-time tally with no recency, so a failure that
  was diagnosed and fixed days earlier read exactly like one happening now: one machine reported
  `runtime_gate_unsafe: 97` for two days after issue #273's fix ended it, and a `max_ms` of 60001
  from a single pre-fix pass alongside a healthy median of 789 ms. Every count is now paired with
  a count over the last `window_seconds`, every reason carries `first_seen`/`last_seen`, and the
  all-time `max_ms` carries the `max_ts` it happened at next to a `recent_max_ms` for the live
  window. Nothing is discarded — a stale failure is dated rather than dropped — and a row whose
  timestamp cannot be read is never counted as recent (issue #310).

- The `truncated_payload` coverage gap had note call sites and no resolution path, so one
  size-pressure eviction reported the workspace as currently losing observations forever. A save
  that sheds nothing and lands with a headroom margin under the state bound now clears the active
  flag; merely landing under the bound does not, because that is the state an eviction itself
  leaves behind. Renewed shedding reopens it, and `gap_history` keeps the sighting either way
  (issue #310).

- Hook timing rows attributed as little as 14% of the pass they measured, so `hook_budget_exceeded`
  could not distinguish real work from queueing. Time spent acquiring the interprocess store lock
  is now reported as `store_lock_wait` wherever in the pass it occurs, including on the timeout
  path; the two formerly unwindowed regions — workspace resolution and the consent probe before
  the store window, advice selection and the stdout write after the drain window — are reported as
  `resolve` and `deliver`; and whatever the partition still misses is reported as `unattributed`
  rather than left for a reader to derive. A contended pass that spent 707 of 711 ms queueing
  previously showed `store: 4` and nothing else (issues #310 and #311).

### Security

- Losing the session-event monitor no longer auto-re-opens the vault on the next ordinary call.
  Idle, session-lock, and suspend locks describe conditions the service can watch recover; monitor
  loss removes the capability that produces those events for the life of the process, so
  auto-re-ready made that lock momentary and left the service running with session-lock relock
  silently no longer applying. A monitor-loss lock now holds until a trusted unlock ceremony.
- This is the first public release; see [`SECURITY.md`](SECURITY.md) for how to report a
  vulnerability. There is no prior version to carry a security fix forward from.

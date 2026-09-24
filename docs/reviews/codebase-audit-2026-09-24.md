# Yoetz codebase audit — 24 September 2026

Read-only review of the `0.3` tree. Twenty-two area reviews covered the kernel, application operations, observation, lineage and projects, semantic egress, the service daemon, vault and upgrades, SQLite, the privacy gateway, provider adapters, host integrations, local capture, protocol and domain models, the CLI, MCP, the terminal UI, config and runtime paths, migrations and scripts, a cross-cutting concurrency and error sweep, performance, tests, and git import. Reviewers read source and tests. No service was started, no live state directory was opened, and no live Yoetz ledger was available in this session.

This document is a findings record. It does not change product behavior. Each finding keeps the reviewer’s severity, the file and lines they cited, the failure they described, and the change they suggested. Overlapping findings are called out in the index so the same defect is not fixed twice under two names.

Four claims were re-read against the tree before this file was written:

- `src/yoetz/application/egress.py` skips the byte cap when `max_bytes` is not strictly positive and then authorizes the full prepared size (`1173`, `1203-1204`). That is P-01 and E-01.
- `src/yoetz/adapters/integrations/codex_plugin.py` hard-codes bare `yoetz hooks …` commands (`154-229`). That is H-01.
- `src/yoetz/adapters/sqlite/recovery.py` returns `SCHEMA_UNSUPPORTED` when `storage_schema_version > 1` (`509-515`), while bundle migrations run through `0015`. That is Q-01.
- `src/yoetz/tui/app.py` paints a check `VERIFIED` only when `verdict == "pass"` (`2057`). That is U-06.

The other findings are the reviewers’ source readings. Confidence is on each item. A high-confidence item can still be a deliberate tradeoff (same-UID writes are outside ADR-004 and ADR-008 in several vault and path notes). Those notes say so.

## How to use this

Fix clusters, not individual paragraphs. The order below is the order that reduces user-visible harm and dishonest receipts first. Performance work should follow the projection-replay cluster, because several correctness fixes sit on that same path.

Suggested change shape, repeated where the reports are specific:

1. Name the invariant in one place (a shared predicate, a closed gap set, a catalog port method) and call it from every surface that currently reimplements it.
2. Add the negative test the report names. Do not add a sleep to infer success.
3. Keep the honesty rules: a missing payload is not “no check”, a blocked child is not “0 unresolved findings”, a failed scan is not `secret_scan.passed=true`, and a zero ceiling is not unlimited.

## Executive summary

About 150 findings. None of the reviewers found `shell=True`, `os.system`, `pickle`, `yaml.load`, or `eval` on the paths they searched. SQL is not built by interpolating user strings. Peer-UID checks on the control socket, vault Argon2id parameters, fresh AES-GCM nonces, and `hmac.compare_digest` for passphrase checks held up. The defects that did show up cluster.

### Receipts and projections can describe a ledger that is not the one on disk

- **K-01.** An unreadable `check_recorded` payload clears tested state and the stale bit. The receipt then says no check was recorded. The sentence for an unreadable check never runs. Compact status can stay `current`.
- **K-02.** `ProjectionState` rejects more than 64 coverage-gap markers. A legal redaction or 65 unknown events makes an intact prefix unreplayable.
- **K-03.** A blocked child selects `unresolved_findings_remain` while the summary and compact sentence count only parent findings. The prose can say nothing is unresolved.
- **K-05.** A repeated action, result, evidence, or finding id silently replaces the prior row, including a resolved finding’s proof edge.
- **L-01.** A parent with no `child_dependencies_recorded` event completes as if it had no children. `delegation_declared` is ignored, and a failed sweep returns no gap.
- **U-01 through U-07.** The terminal UI mislabels obligations, findings, receipts, service health, and external review, and it paints every real check verdict as unproven because it compares to `"pass"`.

### Privacy and egress ceilings fail in the unsafe direction

- **P-01 / E-01.** `max_bytes=0` or `max_tokens=0` means unlimited, up to the type cap. Lowering a positive cap to 0 is classified as a tightening and can commit without a human decision. `min(65536, 0)` stays 0.
- **P-06.** `sensitive_confidential` is never assigned, so a minimal-external ban on that class blocks nothing. Production candidates use JSON pointers; the never-send prefixes exist in tests.
- **P-03.** A never-send block is stored as a clean scan (`secret_scan.passed=true`, `match_count=0`).
- **X-02 / E-02.** A local-model review is returned as success when the disclosure receipt write fails.
- **X-03.** An unreadable egress audit row is treated as authorization never spent.
- **E-03.** A provider attempt that already returned is sent again if the post-result ledger write fails.
- **C-02 / C-03.** Session JSONL import stores commands and tool output with no secret redaction, and `redact_sensitive_content` keeps secrets past a 128-match cap.

### Identity, registration, and recovery

- **H-01.** Codex project-plugin hooks and MCP still launch a bare `yoetz`. A `PATH` entry can replace the live service. External Codex registration was already pinned to an absolute launcher.
- **H-02 / H-03.** Host admission and the Codex project-plugin installer accept non-canonical project roots that the marketplace writer rejects.
- **Q-01.** Public bundle recovery treats every schema version above 1 as unsupported. This binary writes version 15, so the recovery entry never finishes an interrupted write or rebuilds a projection for a current bundle.
- **Q-02.** Ledger recovery reads the event chain, object payloads, and operations with no `BEGIN`. Another connection can commit in that window, and the torn image is kept.
- **D-01.** Each cancelled or timed-out secret prompt permanently consumes one of 128 accept slots. After enough abandoned passphrase prompts, secret entry stops until the process exits.
- **D-03.** Supersede signals the pid in the lock file and does not check that the flock is still held.
- **T-01.** A host `notifications/cancelled` cancels the MCP handler before the control-cancel frame is written. A stopped `start` or `publish_work` can still commit until the 30-second deadline.
- **S-02.** A completed receipt cannot be replayed once the service version digest changes, and the conflict names no receipt.
- **S-03.** Consumed attach-handle replay misses a completed start on the in-memory catalog.
- **V-01.** Snapshot install copies members without re-checking the digests import already verified.
- **V-02.** A failed passphrase initialization can leave an IVK-bound index that the next attempt reports as `vault_tampered`.
- **C-01.** A failed stream admission can store the same rollout line twice, because the source identity hashes a chunk-relative offset.
- **C-04.** An unreadable workspace JSON file is loaded as empty and then saved over the real file.
- **B-01 / B-02.** A UTF-8 BOM lets `[include]` through the git config gate. Submodule `git status` runs before rejection and drops the safe `-c` prefix.
- **G-02.** `provision_test_instance.py` checks `--base` lexically, then `chmod` and `rmtree` it. A symlink base bypasses the path bans.

### Speed

Ordinary publish, check, respond, and receipt replay the task ledger from genesis, and each folded event copies projection maps. One append on `n` events is about `O(n^2)` under the task lock (Z-01, K-06). Receipt and status repeat that replay (Z-02, S-04). Recovery decrypts every payload into a process-lifetime tuple (Z-03). Every check phase rewrites every historical operation blob (Z-04). Approved-check verification repeats a double Git snapshot, up to 64 MiB of diff and a 10,000-file walk, several times per job (Z-06, B-05). Every public RPC compiles a new JSON Schema validator (Z-07, M-06). Codex prompt hooks still import the full Typer application (I-01, Z-08).

### What not to “fix”

Reviewers explicitly cleared, among other things: control-socket peer checks, frame size caps, vault KDF and nonce construction, passphrase comparison, destination pinning and TLS verification on the provider transport, the 1 MiB body cap, `max_retries=0`, import size caps, the absence of shell injection in the git callers they read, and the observation logger’s refusal to interpolate exception text. Same-UID symlink replacement under the owner’s data directory is called out only where a ceremony claims to have verified bytes it later installs without checking again (V-01), or where a check is weaker than a sibling writer (F-03, F-04, V-03).

## Deduped index

| Ids | Topic |
| --- | --- |
| P-01, E-01 | Zero byte or token ceiling is unlimited, and lowering to zero is a silent tightening |
| X-02, E-02 | Local semantic success returned after the disclosure receipt write fails |
| K-06, Z-01, Z-02, Z-05, Z-14, Z-15, S-04 | Full-prefix replay and repeated ledger scans on publish, check, status, and receipt |
| Z-07, M-06 | A new Draft 2020-12 validator is compiled on every public validation |
| I-01, Z-08 | Hook and MCP startup import the full CLI and schema stack |
| Z-06, B-05 | Git status and binary diff run twice, and they run before the file cap |
| K-03, L-01 | Child work is missing from receipt prose, or missing from the manifest entirely |
| U-06, T-01 | A live check or RPC is not what the UI or the MCP cancel notification says it is |
| O-01, O-02 | Permanent observation loss and a failed inspection can still look healthy |

## Counts by area

| Area | File in this document | Findings |
| --- | --- | --- |
| Kernel | 01 | K-01 … K-06 |
| Start, publish, check, receipt | 02 | S-01 … S-04 |
| Observation pipeline | 03 | O-01 … O-06 |
| Lineage and projects | 04 | L-01 … L-09 |
| Semantic egress | 05 | E-01 … E-05 |
| Service daemon | 06 | D-01 … D-03 |
| Vault and upgrades | 07 | V-01 … V-04 |
| SQLite | 08 | Q-01 … Q-08 |
| Privacy | 09 | P-01 … P-08 |
| Providers | 10 | R-01 … R-08 |
| Host integrations | 11 | H-01 … H-06 |
| Local capture | 12 | C-01 … C-10 |
| Protocol and domain | 13 | M-01 … M-07 |
| CLI | 14 | I-01 … I-04 |
| MCP | 15 | T-01 |
| Terminal UI | 16 | U-01 … U-12 |
| Config and runtime | 17 | F-01 … F-06 |
| Migrations and scripts | 18 | G-01 … G-07 |
| Cross-cutting | 19 | X-01 … X-09 |
| Performance | 20 | Z-01 … Z-15 |
| Tests | 21 | A-01 … A-15 |
| Git and import | 22 | B-01 … B-05 |

## Suggested change program

The reports below already contain the test each change needs. This is only the grouping.

### 1. Honesty of checks, receipts, and the terminal UI

- Keep an unreadable check as a coverage fact. Emit `check_payload_unavailable`. Do not clear `stale_after_material_change`. Make compact status use the same predicate as the receipt (K-01, U-01, U-02, U-03).
- Collapse or reject gap markers before `ProjectionState` throws, using the same bounded rule the case builder already uses (K-02).
- Count tested blocked children in the receipt sentence, or do not let them select `unresolved_findings_remain` while the sentence says zero (K-03).
- Treat a missing child manifest as a gap. A recorded empty aggregate remains the clean “no children” case (L-01).
- Compare the terminal UI to real verdict tokens (`no_issue_detected`, `action_required`, and the rest). Say that Ctrl+C does not cancel a check that was already sent (U-06, U-07, T-01).
- Write the Codex hook cancel frame with a shield around the control-cancel send so `CancelledError` does not abort it (T-01).

### 2. Privacy ceilings and review receipts

- Treat `0` as “send nothing”, not “no ceiling”. A decrease to zero is a human decision, not a silent tightening. Re-check `authorization.max_bytes` at dispatch (P-01, E-01, P-02).
- Assign `sensitive_confidential` on the production pointer path, or stop advertising a ban that no classifier emits (P-06).
- Record a never-send block as a failed scan, with the enforcer’s real profile digest (P-03).
- If `complete_local_disclosure` raises, do not return success and do not select that judgment (X-02, E-02).
- If the audit row cannot be read, fail closed. Do not report the authorization as unspent (X-03).
- Do not redispatch a provider attempt whose result already came back (E-03, O-06, R-08).
- Redact import plans with the same helper as hook capture, and do not stop at 128 matches by dropping the rest of the secret (C-02, C-03).

### 3. Registration, recovery, and the control plane

- Pin Codex project-plugin hooks and `.mcp.json` to the absolute launcher, the way Claude, Cursor, and Codex external `mcp add` already do (H-01). Add uninstall for that project tree (H-04).
- Use the marketplace root check (`absolute() != resolve()`, owner, and writability) in host admission and plugin install (H-02, H-03).
- Teach `recover_bundle` the current schema version, or stop advertising it as the recovery entry for version 15 bundles (Q-01).
- Hold one immediate transaction across recovery’s event, object, and operation reads (Q-02).
- Release the secret-ingress accept slot on `CancelledError` (D-01). Put a deadline on human-control reads, and do not hold both dispatch gates across that read (D-02).
- Corroborate the singleton flock before `SIGTERM` (D-03).
- Re-hash snapshot members at install, not only at import (V-01). Make passphrase initialization crash-safe so a marker failure does not orphan an IVK index (V-02).
- Share the ledger’s receipt-suffix rule with publish dry-run (S-01). Replay a completed receipt from caller identity, not from the service version digest (S-02). Look up memory-catalog operations by `(installation_id, operation_id)` (S-03).

### 4. Observation integrity

- One closed set of lifecycle-material gaps, used by health, hook-observed, and advice. Permanent loss is not `ACTIVE` (O-01).
- Do not store a failed inspection as the current snapshot (O-02).
- Set approved-check currency from the pre/post fact. Do not run the check again after a committed receipt (O-03).
- Hold the capture lock until the ticket is deleted or the append has failed (O-04).
- Apply session fair share to protected rows by calling `evaluate_admission` (O-05).
- Hash stream identity with a source offset that survives a replayed chunk (C-01). Do not replace an unreadable workspace file or observation key with an empty one (C-04, C-05).

### 5. Replay cost

- Fold only new records onto the resident projection and `ReplayIndex` (Z-01, K-06, Z-14).
- Pass that projection into receipt, respond, and status instead of replaying the prefix three or four times (Z-02).
- Recover from a projection snapshot plus a tail. Decrypt on demand (Z-03, Z-10, Z-11).
- Update only dirty operation rows (Z-04).
- Read counterpart findings from the resident projection (Z-05, S-04).
- Cache one compiled validator per schema id (Z-07, M-06).
- Fast-path `hooks user-prompt-submit`, `hooks session-start`, `hooks post-tool-use`, and `mcp serve` the way `hooks observe` is already fast-pathed (I-01, Z-08).

### 6. Git and path gates

- Strip a UTF-8 BOM before the `[include]` / `[filter]` scan (B-01).
- Reject submodules before `git status`, and keep the safe `-c` prefix on any child git that still runs (B-02).
- Do not follow symlinks for `.git/objects` and `.git/refs` (B-03).
- Fail the network-filesystem probe closed (F-01). Check ancestors, and do not treat a failed `stat` as success (F-02).
- Open the runtime pin and instance identity with `O_NOFOLLOW` after the `lstat` check (F-03). Use the owner-only writer for explicit config and privacy desired-state (F-04).

## Area reports

The sections below are the area reports, lightly retitled. Line numbers are from the tree at review time.


---

# Kernel

Source notes: `01-kernel.md`.

# Kernel audit (read-only)

## Executive summary

The projection fold is careful about plan versions, obligation resolution, and claim-correction edges, and several honesty fixes (issue #307 freshness, ADR-025 limitation scope) are actually implemented. Four problems still break that standard.

The worst is an unreadable `check_recorded` event. A present-redaction envelope whose payload object cannot be loaded is a legal `AcceptedEvent`. The reducer drops `latest_tested_state` and forces the stale bit off, so a ledger that was `stale_after_material_change` becomes `current` with no tested check. Receipt and capacity code then emit `check_not_recorded`. The honest `check_payload_unavailable` wording is unreachable on this replay path. Compact status, which returns the newest envelope coverage when `latest` is missing, can stay `current` and disagree with the receipt.

Second, `ProjectionState` rejects more than 64 coverage-gap markers. A legal redaction may name 64 events and 64 objects, and unknown-schema events each add their own marker. The 65th marker raises `invalid_projection_state` during the fold, so an intact prefix will not replay. The case builder already collapses some citations for this bound; the reducer does not.

Third, a blocked child selects `unresolved_findings_remain` while the summary and compact sentence count only parent findings. A clean parent plus one blocked child renders as “No actionable findings remain unresolved” and “0 unresolved findings remain.” Later, untested manifests are included in that child set. No receipt test covers the branch.

Fourth, coordination declarations and dispositions cite obligations and findings that the missing-ref scan never sees, so a dangling coordination binding leaves freshness `current`. Separately, action, result, evidence, and finding rows are replaced on a repeated logical key, unlike plans, which corrupt. Every accepted event also rebuilds secondary effects and missing refs from scratch, so replay is quadratic in ledger length.

## K-01

- id: K-01
- severity: high
- category: correctness
- title: An unreadable check erases tested state, clears staleness, and is reported as “no check”
- location: `src/yoetz/kernel/reducers.py` lines 1027–1056, 1082–1097, 1219–1235; `src/yoetz/kernel/receipt_capacity.py` lines 142–178; `src/yoetz/kernel/receipt_builder.py` lines 1012–1021; consumer `src/yoetz/adapters/memory/ledger.py` lines 913–916; loader `src/yoetz/adapters/sqlite/repository.py` lines 629–641; `src/yoetz/domain/events.py` lines 4264–4266
- evidence: `AcceptedEvent` allows `redaction=present` with `payload is None`. The SQLite loader produces that shape when `open_verified` raises `KeyError`, `OSError`, or `ValueError`, or returns no chunks: it keeps the stored redaction and leaves `payload` unset. `reduce_event` initializes `stale` from `freshness is STALE_AFTER_MATERIAL_CHANGE`. `check_recorded` is not a material family, so the incoming event does not refresh that bit. If the check payload is `None`, the branch sets `latest = None` and `stale = False` and does not add a coverage gap. `_freshness` then returns `CURRENT` whenever there is no redacted, unknown, or missing marker. `receipt_gap_codes` and `application/receipt.py` add `check_not_recorded` exactly when `latest is None`. `check_payload_unavailable` is only added when `latest` is still set and the record payload is not a `CheckRecordedPayload`, which this fold never leaves behind. `compact_status_coverage` returns the newest envelope coverage when `latest is None` and does not add a check gap. Its own comment says status and receipts must use the same applicability predicate.
- why it is a bug: Record a check, then a cooperative action. The projection is `stale_after_material_change` and still names that check. Append or reload a later `check_recorded` whose payload object cannot be read (vault/object miss, or a transient `OSError` swallowed by the loader). Replay now has `latest_tested_state is None` and `freshness is CURRENT`. The receipt text is “No check is recorded for frontier N”, which is false: a check event is in the prefix and its verdict was not read. The dedicated sentence “A check is recorded … but its payload could not be read” never runs. Compact status can stay `current` because it trusts the newest envelope coverage, so status looks cleaner than the receipt. A previous good check is also discarded; resolution is not applied on the unreadable fold, so a checkpoint built while the payload was readable and a recovery replay with the payload missing disagree about both freshness and which check applies.
- suggested change: Treat an unreadable `check_recorded` as a coverage fact, not as the absence of a check. Keep a projection row (or a sentinel on `latest_tested_state`) whose source event is the check, with `payload is None`, so `_expected_unavailable_events` emits `event_payload_unavailable` the same way it does for an unreadable action (`test_payload_unavailable_does_not_invent_redaction_marker`). Do not assign `stale = False` unless a `redaction_recorded` actually targets that check; an unreadable check must not outrank `STALE_AFTER_MATERIAL_CHANGE`. In `receipt_capacity.py` and the receipt context builder, if any `check_recorded` envelope exists, `latest is None`, and that envelope is not logically redacted, emit `check_payload_unavailable` rather than `check_not_recorded`. Make `compact_status_coverage` add the same code so the status headline cannot stay `current`. Test: fixture prefix check → material action (assert stale) → replace the next `check_recorded` payload with `None` while leaving `redaction=present`; assert freshness is not `CURRENT`, the gap code is `check_payload_unavailable`, and status coverage includes that code. A second case with `redaction=logically_redacted` plus a `redacted_event` marker should still not say the check was never recorded.
- confidence: high
- related tests: `test_payload_unavailable_does_not_invent_redaction_marker` (action tombstone only); `test_retained_check_freshness_outlives_the_event_that_recorded_it`; `test_redacting_a_partial_check_clears_its_carried_freshness` (real redaction, not a missing payload). None cover an unreadable check.

## K-02

- id: K-02
- severity: high
- category: correctness
- title: The 64-gap projection cap makes a legal prefix unreplayable
- location: `src/yoetz/kernel/projections.py` lines 98 and 542–543; `src/yoetz/kernel/reducers.py` lines 1086–1089 and 1268–1283; `src/yoetz/domain/events.py` line 255 (`MAX_REF_LIST = 64`); `src/yoetz/kernel/deterministic_checks.py` lines 1696–1733 (the case layer already collapses dangling event citations)
- evidence: `ProjectionState.__post_init__` raises `invalid_projection_state` when `len(coverage_gaps) > 64`. Unknown events each append `unknown_event:{event_id}:{name}@{version}` and keep every previous marker. A `redaction_recorded` payload may carry up to 64 target events and 64 target objects. The reducer adds one `redacted_event` marker per target event and, when the object has an evidence association, one `redacted_object` marker per target object. Those sets are unioned with whatever missing-ref and unknown markers `_recompute_missing_gaps` retains. Nothing collapses or evicts markers before `ProjectionState` is built. `invalid_projection_state` is not in the receipt builder’s `STORAGE_CORRUPT` reason list (`application/receipt.py` around 802–805), and `replay()` sits outside that `try`.
- why it is a bug: Replay is supposed to be a total function of an accepted prefix. Two legal prefixes fail the fold. (1) Sixty-five `UnknownEvent` records: the 65th construction raises, so a forward-compatible ledger stops projecting. (2) One redaction of 64 events plus one captured object, or 64 not-yet-published obligation refs on a plan plus one existing unknown or redacted marker: 65 markers, same exception. Later events that would have resolved the missing refs never run, because the fold dies on the earlier event. The task looks corrupt even though every envelope is well-formed. The case builder’s comment states the opposite policy for finding/event citations: many dangling refs become one `missing_ref:cited_event_absent` so the 64-gap receipt bound cannot be exhausted by cardinality.
- suggested change: Give the projection the same bounded-collapse rule the case layer uses, or raise the projection cap strictly above the maximum markers one accepted event can introduce (64 event targets + 64 object targets + retained markers) and reject that at admission with `LIMIT_EXCEEDED` before commit. Do not let `ProjectionState` throw out of `reduce_event` for a quantity the event schema allows. Prefer collapsing repeated `unknown_event` and `missing_ref` tails into one counted marker once the bound is reached, while still keeping freshness at `partial` or `redacted_gap`. Add a reducer test that replays 65 unknown events and a test that replays one object-and-event redaction at `MAX_REF_LIST`, and assert either a successful partial/redacted projection or a typed limit error, never `invalid_projection_state` mid-prefix.
- confidence: high
- related tests: `test_finding_event_subject_ref_outside_prefix_does_not_overflow_projection_gaps` (evt subject refs are omitted specifically to avoid this cap). None overflow the cap on purpose.

## K-03

- id: K-03
- severity: high
- category: correctness
- title: Child actionable findings flip the conclusion without being counted in the prose
- location: `src/yoetz/kernel/receipt_builder.py` lines 824–856 and 1130–1136; `src/yoetz/kernel/lineage.py` lines 649–656 and 1011–1029; compact renderer `src/yoetz/domain/receipts.py` lines 1380–1384 and 1600–1610
- evidence: `_conclusion` returns `UNRESOLVED_FINDINGS_REMAIN` when `lineage.actionable_finding_ids` is non-empty, before the insufficient-coverage branch. `actionable_finding_ids` is every finding id on a child whose rollup state is `BLOCKED`. `with_later_manifest` appends a full `_snapshot_rollup` for each child that was not in the tested manifest, and that rollup can already be `BLOCKED`. The summary sentence is built only from `unresolved_actionable_count`, which is `len(unresolved_actionable)` over parent `finding_states`. Zero uses `_count_phrase` and yields “No actionable findings remain unresolved…”. The findings section uses the same parent count and, when it is zero and the conclusion is not `INSUFFICIENT_COVERAGE`, says “No findings remain open.” `render_receipt_compact` counts `unresolved_findings_for_render`, which is parent `document.findings` minus resolved ids, so a child-only block becomes “0 unresolved findings remain.”
- why it is a bug: Parent check is clean (no parent actionable rows). The recorded child manifest, or a later untested manifest, contains an accepted child with one unresolved actionable finding. The receipt’s conclusion enum is `unresolved_findings_remain`, but the summary, the findings section, and the compact sentence all say that nothing is unresolved. A reader who trusts the sentence under-counts the block; a machine that trusts the enum over-counts parent findings. `with_later_manifest` makes this worse: its comment says it does not change the tested outcome and that child-specific gaps stay off the conclusion-level coverage, but the new child’s `BLOCKED` state still feeds `actionable_finding_ids` and therefore selects the stronger conclusion. The intended limitation for an untested manifest is `lineage_manifest_uncovered` → `INSUFFICIENT_COVERAGE`, which this ordering skips. `tests/unit/kernel/test_receipt_builder.py` never passes a `LineageEvaluation`.
- suggested change: Count only tested children (`tested_manifest_ref is not None`) in the conclusion, matching `application/receipt.py` which already ignores lineage gaps whose child is not a blocking tested rollup. For those tested blocked children, either add their finding count to the summary and compact sentence (“1 unresolved child finding remains”) or keep the conclusion at `INSUFFICIENT_COVERAGE` / a dedicated child-gap conclusion and name the child ids in the children section only. Do not append an untested child’s `BLOCKED` rollup into `actionable_finding_ids`; attach `later_manifest_ref` and `lineage_manifest_uncovered` only. Test: build a receipt with an empty parent finding list, one accepted blocked child, and no other gaps; assert the conclusion and the summary/compact sentence name the same count. Repeat with `with_later_manifest` adding a new blocked child and assert the conclusion stays insufficient coverage rather than “0 unresolved findings remain.”
- confidence: high
- related tests: `test_rejected_child_with_actionable_finding_cannot_escape_blocking_rollup` and `test_conclusion_selection_matches_state_strength` (parent findings only). No receipt test supplies `lineage`.

## K-04

- id: K-04
- severity: medium
- category: correctness
- title: Coordination obligation and finding citations never become missing-ref gaps
- location: `src/yoetz/kernel/reducers.py` lines 935–1024, especially 1020–1023; payload fields `src/yoetz/domain/events.py` lines 1223–1279
- evidence: `_recompute_missing_gaps` walks plans, obligations, decisions, assignments, actions, results, claims, findings, responses, and `coordination_dispositions.evidence_refs`. It does not take `coordination_declarations` or `coordination_contexts`. A `CoordinationObligationDeclaredPayload` is documented as a binding to an existing obligation and carries `obligation_id`, which is never passed to `_target_visible`. A disposition also carries `obligation_id` and an optional `finding_id`; only `evidence_refs` are checked. Other families record `missing_ref:{source_event}:{target}` for the same id kinds, and `_freshness` turns any `missing_ref:` marker into `PARTIAL`.
- why it is a bug: A service-stamped `coordination_obligation_declared` (or a disposition) names `obl_…` or `fnd_…` that is not in the projection. Replay succeeds with no `missing_ref` marker and, if nothing else is wrong, `freshness=CURRENT`. The case layer only re-encodes projection markers, so the receipt does not gain a missing-ref gap either. The ledger asserts a coordination binding the projection cannot see. That is the same class of hole the finding-subject `evt_` exclusion was written to avoid for cardinality, but here the citation is a normal in-projection id and is dropped entirely rather than collapsed.
- suggested change: Pass `coordination_declarations` and `coordination_dispositions` into the missing-ref fold. Require each declaration’s `obligation_id`, and each disposition’s `obligation_id` and `finding_id` when set. Keep event-id citations (`detection_id`) on the existing collapsed path in `build_deterministic_case` if a per-event marker would threaten the 64-gap cap (K-02). Test: declare a coordination obligation before `obligation_published` and assert one `missing_ref` and `freshness is PARTIAL`; publish the obligation and assert the marker disappears; repeat for a disposition `finding_id` that was never recorded.
- confidence: high
- related tests: `test_evidence_claim_response_redaction_paths` and the all-family replay digest. None assert coordination dangling refs.

## K-05

- id: K-05
- severity: medium
- category: correctness
- title: Repeated action, result, evidence, and finding ids silently replace the prior row
- location: `src/yoetz/kernel/reducers.py` lines 1101–1104 (plans reject a repeated key), 1149–1178 (actions, results, evidence, findings assign through), 1179–1185 (responses also assign through, keyed by finding id)
- evidence: `plan_published` / `plan_revised` raise `projection_corrupt` when the plan version is already present. Assignments, decisions, and coordination rows are keyed by `event_id`, which the replay index forces unique. `action_recorded`, `result_recorded`, `evidence_recorded`, and `finding_recorded` are keyed by logical id and overwrite the dict entry, including a newer `source_event_id`. A second `finding_recorded` builds a fresh `FindingProjectionRecord` whose `resolved_by_check_event_id` defaults to `None`, so a prior proof edge is dropped. Evidence associations in `ReplayIndex` accumulate for every event, but the projection payload is only the latest one. I found no kernel test or comment that authorizes this for actions, results, evidence, or findings. Claim v1.0 republish and response latest-wins are explicit; v1.1 claim ids must be fresh.
- why it is a bug: Two accepted `evidence_recorded` events share `evd_…` and disagree about `captured_object_id` or `content_digest`. Claims and results cite that id. After the second event, every reader of the projection sees the new bytes as what the earlier claim supported. The first event remains in the ledger, so genesis replay and a projection checkpoint that was stored before the second event describe different evidence for the same id. The same replacement lets a later `action_recorded` change `command` and `obligation_refs` under an id a result already points at. `command_attempts` then treats the earlier observed event as unknown because `source_event_id` no longer matches, which hides a mismatch instead of preserving both attempts. A repeated `finding_recorded` after a qualifying check clears `resolved_by_check_event_id` and makes a resolved issue current again without the check returning it.
- suggested change: For action, result, evidence, and finding ids, raise `projection_corrupt` when the key is already present, matching plans, unless a written rule defines a legal update (the way obligations and v1 claims do). If response replacement is intentional, leave it and state that in the reducer; do not let finding replacement clear a proof edge unless the new payload’s `issue_key` differs and a check re-fires it. Test: a second `evidence_recorded` with the same id and a different digest raises `projection_corrupt`; a second `finding_recorded` does not clear `resolved_by_check_event_id` on a different event. I could not prove the publish path rejects these duplicates before they reach the reducer.
- confidence: medium
- related tests: `test_republishing_a_superseded_v1_claim_id_does_not_make_it_current_again` (claims only). None found for a repeated action, result, evidence, or finding id.

## K-06

- id: K-06
- severity: medium
- category: performance
- title: Every accepted event rebuilds secondary effects and missing refs from the whole projection
- location: `src/yoetz/kernel/reducers.py` lines 835–908 (`_recompute_secondary_effects`), 935–1024 (`_recompute_missing_gaps`), 1304–1323 (called for every non-unknown accepted event), 358–421 (`extend_replay_index` copies the reverse maps and re-sorts evidence associations); `src/yoetz/kernel/policies/work_integrity.py` lines 375–396; `src/yoetz/kernel/finding_resolution.py` lines 382–407
- evidence: `reduce_event` copies every collection, then `_recompute_secondary_effects` clears and rewrites plan supersession, obligation plan-change, decision supersession, and the contradiction map by scanning all of those rows. `_recompute_missing_gaps` scans every row and every ref again. Both run for session, lineage, and receipt events that do not change those collections. `extend_replay_index` allocates new dicts and, for each evidence artifact, sorts the association tuple. `work_integrity`’s unresolved-action rule nests a scan of all later actions inside each action. Resolution explanations call `replay` on the pre-check prefix per candidate check. A ledger of length n with a proportional number of actions or claims therefore replays in Θ(n²) time and allocations. I did not measure a live task, so I am not claiming a specific latency.
- why it is a bug: Hook observation appends one event at a time and replays or extends the projection on the service path. The quadratic term is paid on every observation, not only on redaction. Redaction really does invalidate derived edges, which is why a full rebuild is conservative, but running it for `session_resumed` or an empty child-dependency record does not buy correctness. The action-without-result nest is the same shape inside every check.
- suggested change: Recompute plan, decision, and contradiction edges only when the folded family is `plan_published`, `plan_revised`, `decision_recorded`, `claim_recorded`, or `redaction_recorded`. Recompute missing refs from the refs of the folded event plus refs of any tombstoned rows, not from every historical row, and keep a regression test that a redaction in the middle of a mixed prefix matches genesis replay (`test_replay_extension_matches_genesis_replay_for_mixed_prefixes` is the oracle). For actions, index by subject key once per check instead of the nested loop. Cache the historical proof projection the way `finding_resolution_explanation` already caches per check, and do not call `replay` again for non-command findings.
- confidence: high on the complexity, medium that it is worth changing before a profiled ledger shows it
- related tests: `test_replay_extension_matches_genesis_replay_for_mixed_prefixes` (equivalence, not cost). None found that bound replay time.

## Not reported

I did not treat the following as defects after reading the code and the locking tests: open→resolved obligation rules; v1.1 limitation scope agreeing with relevance when the action row is redacted; proof-based resolution refusing suppressed, stale, and unreadable checks; empty child-dependency inventories not superseding a check; payload-unavailable action rows not inventing a `redacted_` marker (the case layer still marks those projection rows unavailable); pending children staying annotation-only. Singular summary grammar (“One actionable finding remain unresolved”) is asserted by `test_resolved_history_beside_a_current_finding_counts_only_the_current_one` and is a wording nit, not a state bug.


---

# Start, publish, status, check, respond, receipt

Source notes: `02-start-publish-check.md`.

# Audit: start, publish, status, check, respond, receipt

Read-only review of the application operations listed in the scope. No service was started and no ledger was opened. Findings below are limited to behaviors that disagree with a neighboring contract or that do work the surrounding code already knows how to avoid. Style nits are omitted.

## Executive summary

Four defects are solid enough to report. A publish dry-run rejects a frontier that the real append accepts when the only newer event is `receipt_recorded`. A completed receipt whose service-injected version digest no longer matches cannot be replayed, and unlike publish the conflict carries no locator. Consumed attach-handle replay never sees a completed start on the in-tree memory catalog because it looks up operations by a string key the catalog does not use. Status, project views, and post-commit check advice rescan whole task ledgers, including a historical status read that only needs one event.

Sqlite start replay via `_operation_by_key` is not affected by the memory-catalog miss. No lost-write or partial-commit path in respond, receipt, or publish was found that abandons objects after `run_prepared_append` has been entered; those paths shield the append and refuse pre-append cleanup after submission.

## S-01

- **severity:** medium
- **category:** correctness
- **title:** Publish dry-run rejects a receipt suffix that `append_batch` accepts
- **location:** `src/yoetz/application/publish_work.py` lines 1307–1355 (`_preflight_dry_run_feasibility`); ledger rule at `src/yoetz/adapters/memory/ledger.py` lines 210–211 and 1623–1634 (Sqlite append delegates to this oracle)
- **evidence:** The dry-run docstring says it matches `append_batch` acceptance for the frontier sequence. The implementation treats a stale `expected_frontier` as legal only when every later record is observation-authored:

```1339:1355:src/yoetz/application/publish_work.py
        expected_sequence = int(request.expected_frontier.sequence)
        observation_only_advance = expected_sequence < current.sequence and all(
            is_observation_authored(record)
            for record in existing_records
            if record.ledger.ingestion_sequence > expected_sequence
        )
        if expected_sequence != current.sequence and not observation_only_advance:
            raise PublicOperationError(
                PublicErrorCode.FRONTIER_CONFLICT,
                ...
            )
```

`append_batch` uses a wider predicate, `_receipt_prefix_suffix_safe_unlocked`: a later record is allowed when it is observation-authored **or** its schema is in `_REPLAY_SAFE_IMMATERIAL_FAMILIES`, which is exactly `{"receipt_recorded"}`. Receipt events are authored by `yoetz.engine` with service assurance, so `is_observation_authored` is false for them (`src/yoetz/domain/events.py` `is_observation_authorship`, and the receipt append author in `src/yoetz/application/receipt.py` around the `AppendEntry` for `receipt_recorded`).
- **failure scenario:** A task is at frontier 10. A receipt appends `receipt_recorded` and the head becomes 11. A later `publish_work` with `dry_run: true` and `expected_frontier.sequence == "10"` returns `FRONTIER_CONFLICT`. The same body with `dry_run` omitted is accepted, because the only record past sequence 10 is `receipt_recorded`. Callers that treat dry-run as a gate refuse a batch the ledger would commit. Any other non-observation event in that suffix still conflicts on both paths; the hole is specific to the receipt family the ledger carved out.
- **suggested change:** Share one helper with the ledger predicate (observation-authored, plus `receipt_recorded`, and `finding_free` only for receipt appends). Dry-run of ordinary publish should allow a suffix of only those records and should still reject a suffix that contains any other family. Add a unit test that builds a chain ending in `receipt_recorded`, asserts dry-run returns `would_accept` for `expected_frontier` at the pre-receipt sequence, and asserts a following `evidence_recorded` or `plan_revised` still raises `FRONTIER_CONFLICT`. Assert the non-dry-run append of the same allowed batch commits.
- **confidence:** high
- **related tests:** `tests/unit/application/test_publish_work_preflight.py` covers dry-run feasibility but not a `receipt_recorded` suffix. `tests/conformance/operations/test_publish_work_contract.py` does not lock this case.

## S-02

- **severity:** medium
- **category:** correctness
- **title:** A completed receipt cannot be replayed once the service version digest changes, and the conflict has no locator
- **location:** `src/yoetz/application/receipt.py` lines 240–283 (`_version_json`, `_identity`), 377–392 (`_preflight`), 689–733 (`_replay_result`, never reached on digest mismatch); `src/yoetz/application/status.py` lines 757–770 (non-publish complete operations omit the stored body)
- **evidence:** Idempotency is `lookup_operation(writer_id, request_id)` plus equality of `request_digest`. The digest is not the caller body alone. `_identity` mixes caller fields with `versions`, and `_version_json` includes `package_version`, `engine_version`, `projection_version`, schema versions, policy versions, and `resource_manifest_digest`. Those values come from `app.receipt_versions_for(runtime)`, not from the request. On any mismatch, including a completed row, `_preflight` raises `IDEMPOTENCY_CONFLICT` and does not call `_replay_result`. The error has no `safe_details` locator. `status view=operation` for a complete non-publish operation returns only `found`, `state`, and `operation_kind`. Publish’s mismatched-body path is the opposite: `REQUEST_IDENTITY_CONFLICT` includes sequence, head digest, and accepted count (`publish_work.py` `_request_identity_conflict`).
- **failure scenario:** `receipt` commits and the response is lost (timeout, dropped RPC). The service restarts on a build whose package version or resource-manifest digest differs. The client retries the same request id, task, session, writer, frontier, format, include, and redaction profile. `_preflight` computes a new digest, sees the completed row, and returns `IDEMPOTENCY_CONFLICT` (“The request ID was already used.”). The receipt object and `receipt_recorded` event are durable. The retry will not load them, and operation status will not name the receipt id or object id. A new request id mints a second receipt instead of returning the first.
- **suggested change:** Split caller identity from service versions. On a completed `RECEIPT` operation, replay when the caller-controlled fields match and return the stored document via `_replay_result`; the document already carries the versions that produced it. If the caller-controlled body differs, raise a non-destructive conflict that includes the receipt id, object id, and subject frontier from the operation locator, same shape as publish’s identity conflict. Do not treat `package_version` or `resource_manifest_digest` as part of the key that gates that replay. Test: commit a receipt, swap the version slice, retry the identical `ReceiptRequest`, and assert the same `receipt_id` and digest. Test a real caller-field change (`format`) and assert the conflict names the stored object.
- **confidence:** high
- **related tests:** `tests/conformance/operations/test_receipt_contract.py` replays a receipt only while the version slice stays fixed. Nothing covers a version-slice change against a completed operation.

## S-03

- **severity:** medium
- **category:** correctness
- **title:** Consumed attach-handle replay does not find a completed start on the memory catalog
- **location:** `src/yoetz/application/start.py` lines 1299–1327 (`_replay_check` inside `_execute_handle_attach`); catalog key at `src/yoetz/adapters/memory/start_catalog.py` lines 151 and 1494–1604
- **evidence:** After a handle is consumed, `LineageCoordinator.attach_with_operation` runs the child start again only when `replay_check` returns true. Otherwise it raises `SESSION_CONFLICT` / `attach_handle_reused` before the callback (`src/yoetz/application/lineage.py` around the `consumed_session_id is not None` branch). `_replay_check` first calls `_operation_by_key(request_id)` when that method exists. `SqliteStartCatalog` implements it and stores `state` as the text `complete`, so the production catalog can return true. `MemoryStartCatalogAdapter` has no `_operation_by_key`. The fallback then does `operations.get(request.request_id)` on `MemoryStartCatalogState.operations`, which is `dict[tuple[str, str], _OperationRecord]` keyed by `(installation_id, operation_id)` (`key = (self._installation_id, request.operation_id)`). A string request id never hits. The lookup result is `None`, both `getattr(..., "task_id")` and `state` fail the comparison, and the function returns false. A `PublicOperationError` from the sqlite lookup is also swallowed and treated as “not a replay,” so a corrupt catalog row becomes `attach_handle_reused` instead of `STORAGE_CORRUPT`.
- **failure scenario:** On the memory catalog, parent `start mode=delegate` returns a handle, the child `start mode=attach` commits and consumes the handle, and the response is lost. The child retries the same request id and the same handle. `replay_check` is false, so the retry fails with “The attach handle was already used” even though the child start operation is complete. The child cannot recover the session and writer that were already minted. The sqlite service path still recovers, because `_operation_by_key` is present.
- **suggested change:** Stop probing private dict layout. Add a catalog port method such as `completed_start_task_id(operation_id) -> str | None` implemented by both adapters, and use only that in `_replay_check`. It should return the task id only when state is complete; pending and quarantined stay false; storage errors propagate. Memory lookup must use `(installation_id, operation_id)`. Add a test on `MemoryStartCatalogAdapter` (not a string-keyed fake): complete a handle attach, consume the handle, call `execute_start` again with the same request, and assert `outcome="replayed"` with the same session id. A second test with a different request id still gets `attach_handle_reused`.
- **confidence:** high
- **related tests:** none on this lookup. `tests/conformance/operations/test_start_contract.py` does not exercise consumed-handle replay against `MemoryStartCatalogState`.

## S-04

- **severity:** medium
- **category:** performance
- **title:** Status, project views, and check advice reread entire ledgers
- **location:**
  - `src/yoetz/application/status.py` lines 812–845 (`_exact_frontier`) and 885–909 (`_candidate_page`)
  - `src/yoetz/application/status.py` lines 1144–1178 and the call at 1534 (`_lineage_readiness_gaps` on every view)
  - `src/yoetz/application/task_views.py` lines 70–100 (`lineage_status_page`) and 267–355 (`project_status_snapshot`)
  - `src/yoetz/application/check.py` lines 471–520 (`_current_task_findings`) and 624–628, invoked after commit at 2336–2338
- **evidence:**
  1. Historical status resolves a frontier digest by iterating `load_events(..., through=target)` from genesis and keeping only the last record. `load_events` already accepts `after`, so the event at `target` is `after=target-1, through=target`. The loop does not verify the hash chain; it only checks `found.sequence == target`.
  2. `view=candidate_findings` loads every event through the frontier and `replay`s them on every page, including a cursor continuation that only changes `offset`.
  3. After the page is built, every status view calls `_lineage_readiness_gaps`. If `list_child_task_ids` is non-empty, `lineage_status_page` loads the parent ledger again, then does `task_lineage`, `task_session_states`, and `task_route` per child.
  4. `view=project` repeats that pattern per member: three catalog reads, `load_frontier`, a full `lineage_status_page` (another ledger scan and another per-child catalog loop), then a second `load_events` walk whose only use is the last `ReceiptRecordedPayload`.
  5. After `commit_check_if_current`, duplicate-finding advice calls `_current_task_findings` for every counterpart. Each call routes that task and replays its entire ledger. The check result is already durable; this work only adds optional notes.
- **failure scenario:** A parent task with a few thousand events and several accepted children calls `status view=compact` or `view=operation`. The compact or operation page is cheap, then lineage readiness reads the whole parent chain and one catalog round-trip per child before the response is sent. `view=project` multiplies that by the member count (two full scans per member). `view=candidate_findings` with a cursor repeats a full replay per page. A check on a project member waits, after the verdict is committed, on a full replay of each other member. Latency grows with ledger length and membership, on paths that are supposed to be reads or already-finished commits.
- **suggested change:**
  - Point-read the historical frontier: `load_events(session, after=target - 1, through=target)` and require exactly one record whose sequence is `target`.
  - For candidate pages, replay once per frontier and page the ranked tuple; do not replay again for each cursor. The cursor already binds the frontier and projection version.
  - Skip `_lineage_readiness_gaps` when `list_child_task_ids` is empty (already done) and do not call `lineage_status_page`’s full event load when the caller only needs blocking gap codes. A catalog query of child acceptance and route state is enough for `lineage_child_unavailable`; manifest comparison can stay on `view=lineage`.
  - In `project_status_snapshot`, load each member ledger once and derive both the lineage manifest and the latest receipt from that pass. Add a catalog method that returns lineage, session health, and route for a task id list instead of three calls per id.
  - Cap or share the advisory replay: read counterpart finding keys from the compact projection or a stored finding index, and do not `replay` the full chain after the check has committed. If the projection is missing, omit the note.
  - Tests: a ledger of N events, `at_frontier=N/2`, assert the event iterator is invoked for one sequence (fake ledger that counts yields). A project of M members asserts one `load_events` per member. A check with M counterparts asserts advisory does not call `load_events` M times on the success path, or asserts an upper bound of one projection read per counterpart.
- **confidence:** high
- **related tests:** `tests/unit/application/test_task_views.py` and `tests/conformance/operations/test_status_contract.py` lock page shape, not scan counts. No test counts ledger reads for these paths.

## Coverage notes

Checked and not reported as defects: start’s seven-step resume (lifecycle append is operation-idempotent; contradiction quarantines the catalog row on purpose); publish and receipt abandon staged objects only before `run_prepared_append` or on `PreSubmissionCancelled`; check `fail_check_if_current` requires a still-pending lease, so a post-commit advisory error cannot overwrite a completed check; status cursors bind session, view, filter digest, frontier, and limit; delegated start returning the parent session with the child `task_id` matches `StartSuccessModel` (`attach_handle.child_task_id == task_id`) and the parent next-request template. `resolve_ambiguous_operation` treats a pending non-check as `operation_kind_state_contradiction`; the unit test forces `OperationKind.CHECK` for the pending case, and only checks are durably pending in the ledger.


---

# Observation pipeline

Source notes: `03-observation.md`.

# Observation audit

Read-only review of the observation application and domain modules, plus the admission and verification repositories those paths actually call. No services were started and no live state directory was opened.

## O-01

- **Severity:** high
- **Category:** correctness
- **Title:** Lifecycle can stay ACTIVE, and advice can stay quiet, while permanent observation loss is current
- **File:** `src/yoetz/application/observation_health.py:23-32`, `src/yoetz/application/observation_health.py:105-140`; caller `src/yoetz/adapters/integrations/observation_local.py:8906-8935`; hook-observed gate `src/yoetz/domain/observation.py:1982-1989`; advice filter `src/yoetz/kernel/policies/observation_advice.py:650-686`
- **Evidence:** `compute_observation_lifecycle` treats only six gap codes as material: `service_unavailable`, `vault_locked`, `unpaired_event`, `unsupported_event`, `source_lag`, `cursor_stale`. Any other current gap is ignored. `session_ended` or inactive consent returns `STOPPED` before gaps are considered. `advice_frontier` is stored on `ObservationHealthSignals` and never read.

  `_status_unlocked` passes `_current_gaps()` straight through. That set re-adds live conditions that are not in `_MATERIAL_GAPS`, including `observation_input_loss`, `observation_storage_corrupt`, `ledger_rejected`, `dedup_conflict`, `outbox_overflow`, `outbox_quarantined`, `capture_budget_exhausted`, `content_capture_unavailable`, `payload_too_large`, `verification_stale`, `policy_untrusted`, and `session_superseded`.

  `observation_earns_hook_observed` is true whenever lifecycle is `ACTIVE` and some real evidence exists. It does not look at `status.gaps`.

  `_observation_gaps` uses the same short interesting-set. A lifecycle that is `ACTIVE` or `STOPPED`, plus a gap outside that set, produces no `observation_gap_or_stale` finding.
- **Failure scenario:** A workspace has a fresh hook receipt, an empty pending outbox, a mapping, and one active `observation_input_loss` or `observation_storage_corrupt` gap (quarantine reason, selection-loss history, or a capture-budget gap that remained active after the queue drained). Lifecycle is `ACTIVE`. A later receipt can take `HOOK_OBSERVED`. Advice does not emit the gap finding. If every bound session has ended, the same gaps produce `STOPPED` instead, which reads as a clean stop.
- **Suggested fix and test:** Define one closed set of lifecycle-material gaps and use it in both `compute_observation_lifecycle` and `_observation_gaps`. Include permanent loss, storage corruption, ledger rejection, dedup conflict, quarantine, overflow, capture-budget exhaustion, verification-stale, and payload-too-large. Do not return `STOPPED` while any of those gaps are still current; return `DEGRADED` (consent revocation can still be `STOPPED`, but the gaps stay on the status and in advice). Make `observation_earns_hook_observed` false when any material gap is present.

  Test: build `ObservationHealthSignals` with `gaps=("observation_input_loss",)` and otherwise-healthy fields; assert lifecycle is `DEGRADED` and `observation_earns_hook_observed` is false. Repeat for `observation_storage_corrupt` with `session_ended=True` and assert the lifecycle is not a clean `STOPPED`. Extend `tests/unit/application/test_observation_health.py`, which today only covers `service_unavailable`.
- **Confidence:** high
- **Related tests:** `tests/unit/application/test_observation_health.py` (does not cover these gaps). `tests/unit/kernel/test_observation_advice_policies.py` covers the interesting-set, not input loss or storage corruption.

## O-02

- **Severity:** high
- **Category:** correctness
- **Title:** A failed changed-path inspection is stored as the current snapshot and then never retried
- **File:** `src/yoetz/application/observation_coordinator.py:4844-4952`; load key `src/yoetz/adapters/sqlite/observation.py:902-915`
- **Evidence:** `_prepare_verification_worker` loads an inspection snapshot by workspace, Yoetz session, and subject-state digest. Only a miss enters the inspect path. On any exception it notes `content_capture_unavailable`, sets `relative_paths = ()`, and does not restore `changed_digest`. It then still calls `inspect_recorder` with whatever prefix survived: an empty path list, a digest that may already be the hash of the real path list, and fact or excerpt refs that may be missing or partial.

  `load_inspection_snapshot` returns that row whenever `is_current=1`. The next PostToolUse with the same subject digest takes the load hit and skips `list_changed_relative_paths` entirely. `record_inspection_snapshot` also does not check that `changed_paths_digest` equals the canonical digest of `relative_paths`.
- **Failure scenario:** The first inspection throws after `changed_digest` is computed (encrypt failure, inspect adapter error, or a path listing error). The stored row says this subject state was inspected, with an empty path list and possibly a digest of the paths that were actually changed. Later tool events at the same tree digest reuse the row. Plan-scope advice never sees a new inspect fact. The coverage gap from the first failure is not in the health material set (O-01), so lifecycle can stay `ACTIVE`.
- **Suggested fix and test:** On exception, do not call `inspect_recorder`. Leave no current row for that subject digest so the next event retries. If a partial facts object was staged, abandon it when the snapshot is not committed. When a row is written, set `changed_paths_digest` only from the path list actually stored, and reject a mismatch in `record_inspection_snapshot`.

  Test: patch `list_changed_relative_paths` or `_encrypt_captured_content` to raise after the digest is computed; assert no current snapshot row; call prepare again with the inspect succeeding; assert the stored path list matches the digest. No current coordinator test covers this failure path.
- **Confidence:** high
- **Related tests:** none for this failure. Happy-path inspection is inside `tests/unit/application/test_observation_coordinator.py` only indirectly.

## O-03

- **Severity:** high
- **Category:** correctness
- **Title:** Approved-check currency is dropped, and a retry can publish a second ledger result
- **File:** `src/yoetz/application/observation_verification.py:388-433`, `src/yoetz/application/observation_verification.py:483-504`; ledger identity `src/yoetz/application/observation_coordinator.py:3778-3790`, outcome `src/yoetz/application/observation_coordinator.py:3958-3964`; job identity `src/yoetz/adapters/sqlite/observation_verification.py:32-63`, reclaim `src/yoetz/adapters/sqlite/observation_verification.py:93-108`, insert-once `src/yoetz/adapters/sqlite/observation_verification.py:181-207`
- **Evidence:** `run_bound_approved_check` compares subject state before and after the process. A pass whose post-digest differs from the pre-digest returns a fact with `is_current=False` and status `passed_not_current`. `ObservationVerificationWorker.run_once` discards that fact (`result, _fact`). Currency becomes a third snapshot, `after == job.subject_state_digest`, taken after the check function has returned.

  `_materialize_approved_check` sets `ResultOutcome.SUCCESS` for every `ApprovedCheckStatus.PASSED`, including when `completed.is_current` is false. The operation digest is `job_id`, approval, `result_digest`, task, session, and writer. It does not include `is_current` or `subject_state_after`. A second call with the same check output hits `lookup_operation` and returns without rewriting the receipt. A second call with a different `result_digest` appends a new operation.

  `claim_next` puts an expired `running` job back to `pending` and runs the check again. `complete` inserts the sqlite result with `ON CONFLICT(job_id,check_id) DO NOTHING`, then updates the job status from the latest `is_current`. `enqueue_latest` skips creation when any row already exists for that workspace, policy, approval, and subject digest, regardless of `stale` or `failed`.
- **Failure scenario:**
  1. The check passes while the tree changes, so the inner fact is not current. Before the worker's third capture, the tree returns to the enqueued digest. Sqlite records `passed` and `is_current=1`. Advice (`check.status == "passed" and check.is_current`) treats it as a current pass. The drift the inner function measured is gone.
  2. Materialize commits a pass, then the process dies before `complete`. Two minutes later the lease is reclaimed. The workspace has changed, the check fails or prints different output, and a second ledger graph is appended. Sqlite keeps one row. Ledger and advice no longer describe the same run. If the first receipt said `is_current=true` and the retry would have said false, the ledger receipt stays true forever.
  3. A run finishes `stale` or `failed` for digest D. The tree leaves D and comes back. `enqueue_latest` sees the old job id and does not enqueue. That digest is never checked again.
- **Suggested fix and test:** Set `is_current` from the pre/post fact: a pass is current only when `pre == expected`, `post == pre`, and `post == job.subject_state_digest`. Do not take a later snapshot as a substitute. Include `is_current` and `subject_state_after` in the materialization digest, or refuse to append when a committed receipt disagrees and record the disagreement as a gap. Map non-current passes to a non-success ledger outcome. On lease reclaim, complete the existing job as `stale` when an operation for that `job_id` is already committed, instead of executing the check again. Allow `enqueue_latest` to insert a new state token when the existing row is `stale` or `failed`.

  Tests: a worker double whose subject digest flips during `runner.run` and restores before the third capture must record `is_current=0`. A materialize-then-raise-before-complete, followed by `claim_next` after lease expiry, must not append a second operation. Re-enqueue of a stale digest must create a runnable job. `tests/unit/application/test_observation_verification_worker.py` only asserts the happy-path `is_current=1`.
- **Confidence:** high
- **Related tests:** `tests/unit/application/test_observation_verification_worker.py`, `tests/unit/application/test_observation_verification_supervisor.py`

## O-04

- **Severity:** high
- **Category:** correctness
- **Title:** Capture-only can stage content after the structural snapshot and before ticket deletion
- **File:** `src/yoetz/application/observation_coordinator.py:2045-2051`, `src/yoetz/application/observation_coordinator.py:2107-2282`, `src/yoetz/application/observation_coordinator.py:2378-2384`, `src/yoetz/application/observation_coordinator.py:2751-2758`; gate split `src/yoetz/service/daemon.py:1127-1145`; hook staging `src/yoetz/cli/observe_hooks.py:1711-1771`
- **Evidence:** Structural ingest holds `_lock`, takes `_capture_lock` only around `_capture_content`, then releases `_capture_lock` before `store.ingest` and `_append_materialized`. Capture-only uses `_capture_lock` as its whole request lock, and the daemon does not take the observation gate for a valid capture-only call, so it can run while a sweeper is inside materialize.

  The append uses the in-memory `captured_content` from before that release. After a successful append the same ingest deletes the ticket. The comment at 2380-2384 says a contentless commit must not be strengthened by later content. This window is earlier than that: the extra manifests can be durable before the append returns, and the structural row is still acknowledged.
- **Failure scenario:** The sweeper has snapshotted manifests M1 and is inside the ledger append. The hook drain, which still holds the plaintext chunks, sends capture-only for the same source identity. That call acquires `_capture_lock`, sees the ticket still pending, and stages M2. The sweeper commits an operation bound to M1, deletes the ticket, and the drain acknowledges the row. M2 is not in the operation. Role comparison uses the pre-release batch, so no `content_capture_unavailable` gap is added for M2. A duplicate structural replay will not strengthen the committed operation. If capture-only instead creates a replacement ticket after the delete, nothing structural is left to consume it.
- **Suggested fix and test:** Hold `_capture_lock` until the ticket is deleted or the append has failed and the ticket is intentionally retained. Re-read manifests under that lock immediately before the digest is fixed, and include every complete part in the batch. If the ticket gained parts during append, do not acknowledge; retry the structural row. Add a test with two tasks: one blocked inside `_append_materialized`, one capture-only for the same logical identity; after both finish, the committed artifact set must equal the stored manifests or the outbox row must still be pending.
- **Confidence:** medium
- **Related tests:** `tests/unit/application/test_observation_capture_fence.py`, `tests/unit/application/test_observation_drain.py` (lease and FIFO). Neither interleaves capture-only with an in-flight append.

## O-05

- **Severity:** high
- **Category:** correctness
- **Title:** Live admission does not apply session fair share to protected rows, and almost every row is protected
- **File:** `src/yoetz/adapters/integrations/observation_local.py:1948-1964`, `src/yoetz/adapters/integrations/observation_local.py:4733-4774`; domain contract `src/yoetz/domain/observation_budget.py:408-414`; share size `src/yoetz/domain/observation_budget.py:265-268`
- **Evidence:** `evaluate_admission` rejects any request, protected or not, when projected session count or bytes exceed `session_fair_share` (`queue_count // 4`, 128 on the standard 512 profile). Production `_admission_allowed` does not call `evaluate_admission`. It applies `limits.session_fair_share` only inside `if not protected`.

  `_outbox_row_is_protected` is false only for `RoutineReadSummary` and for a clean `routine_read_detailed` row with no gaps and no content refs. Tool, edit, and failure rows are protected.

  The per-session cap that does apply to protected rows is `resolved.selection.queue_count`. The default selection capacity is standard, 512, which is also the default aggregate. So that cap does not keep one session off another session's share.
- **Failure scenario:** Two host sessions share a workspace. Session A enqueues 512 protected PostToolUse rows. Each passes the session cap and skips fair share. The aggregate queue is full. Session B's next observation is rejected `outbox_overflow` and, when it cannot be replayed, becomes selection loss. The domain function would have stopped session A at 128.
- **Suggested fix and test:** Make `_admission_allowed` call `evaluate_admission` with the same usage, limits, and protected bit, including session fair share for protected rows. Keep the separate state-byte check that already runs on the encoded projection.

  Test: two sessions, standard profile, 129 protected envelopes on session A; the 129th is rejected while session B can still enqueue. Assert the rejection reason is the fair-share outcome, not a full aggregate. `tests/unit/domain/test_observation_budget.py` covers the unused domain function. `tests/unit/adapters/test_observation_admission.py` does not cover this cross-session case.
- **Confidence:** high
- **Related tests:** `tests/unit/domain/test_observation_budget.py`, `tests/unit/adapters/test_observation_admission.py`

## O-06

- **Severity:** medium
- **Category:** correctness
- **Title:** A failed semantic-attempt complete is treated as success, so the provider call can run again
- **File:** `src/yoetz/application/observation_advice_semantic.py:386-398`, `src/yoetz/application/observation_advice_semantic.py:526-559`
- **Evidence:** `ObservationAdviceSemanticWorker._complete` swallows every exception from `repository.complete`. `run_once` still returns the attempt. The supervisor then runs `after_complete` and, on the next `claim_next` miss, retires the handle. The row can still be `running`. `claim_next` later reclaims an expired lease and `dispatch` runs again. Cancellation is the path that records `cancelled` before propagating; a failed `complete` after a successful `dispatch` is not.

  The same supervisor drains one workspace in an inner `while` until that handle is idle, then moves to the next handle. The verification supervisor was changed so one lane cannot hold the loop; this one was not.
- **Failure scenario:** The provider returns and `complete` raises (`SESSION_CONFLICT` after a lease change, or a storage error). The worker reports the attempt done. Advice rebuild runs without a stored outcome. After the lease expires, rediscovery claims the same attempt and sends the packet again. Sibling workspaces wait behind the workspace that still has pending attempts.
- **Suggested fix and test:** If `complete` raises, do not return the attempt and do not run `after_complete`. Leave the row claimable and keep the handle registered. Bound the inner loop to one attempt per handle per round, matching the verification supervisor.

  Test: `repository.complete` raises after `dispatch` returns; assert `dispatch` is not called a second time until a deliberate reclaim, and assert `after_complete` was not called. `tests/unit/application/test_observation_advice_semantic.py` should grow this case.
- **Confidence:** medium
- **Related tests:** `tests/unit/application/test_observation_advice_semantic.py`

## Checked and not reported

- Exception logging in these modules goes through `record_unexpected_exception_without_raising`, which records a reviewed exception class token and a `module:lineno` origin, not `str(exc)`. `ObservationContentChunk.__repr__` and `ObservationEnvelope.__repr__` omit plaintext. No observation content leak into logs or error strings showed up on these paths.
- Drain FIFO, the per-workspace lease, and idempotent ledger replay on `DUPLICATE` are deliberate and tested (`tests/unit/application/test_observation_drain.py`). A lineage failure after a successful append returns `service_unavailable` and retries; that is noisy but the append identity is stable.
- The verification supervisor parks on its wake event when no handle made progress. That is not a busy loop.
- `evaluate_admission` itself is internally consistent. It is not what the live outbox calls (O-05).


---

# Lineage, projects, and coordination

Source notes: `04-lineage-projects.md`.

# Lineage, projects, and coordination audit

Read-only review of the scoped modules. No service was started. Line numbers refer to the tree at audit time.

Attach-handle compare-and-set was reviewed and is not reported. `attach_with_operation` validates before the callback, refuses a second session, and expiry is skipped only when the caller’s `replay_check` returns true. Production `start.py` binds that check to a completed catalog operation for the same request id and task. `request_id` / `request_digest` on the lineage method are format-checked and then unused; the digest check stays in the start callback. No second finding is made from that split.

## L-01

- **Severity:** high
- **Category:** lost child outcomes
- **Title:** A parent with no recorded child manifest completes as if it had no children
- **File:lines:** `src/yoetz/application/lineage_coordinator.py:531-540`, `src/yoetz/application/lineage_coordinator.py:559-598`; cross-check `src/yoetz/kernel/lineage.py:643-646`, `src/yoetz/kernel/lineage.py:1085-1101`, `src/yoetz/kernel/lineage.py:1224`; receipt gate `src/yoetz/kernel/receipt_builder.py:832-837`
- **Evidence:** The coordinator comment says the first sweep must append an empty `children` event so a receipt can tell “inventory ran” from “inventory never ran”. `sweep_task` returns `None` on any routing or sweep exception (`except Exception: return None`) and an inventory failure returns `inventory_read_gap=True` without appending. `lineage_manifest_from_records` returns `LineageManifest()` when no `child_dependencies_recorded` event exists. `evaluate_recorded_lineage` evaluates that empty manifest. `blocks_clean_completion` is false when there are no child rollups and no `lineage_manifest_uncovered` gap. `delegation_declared` is not a manifest event, so a successful delegate does not by itself put the child into the evaluation. Receipt conclusion consults lineage only when the evaluation object blocks.
- **Failure scenario:** Parent delegates a child. The manifest sweep has not succeeded (maintenance not yet run, parent route not active, or `sweep_task` swallowed an error). Parent check and receipt replay the parent ledger, see no aggregate, and treat the child set as empty. The child’s findings, missing receipt, or still-open work never enter `blocks_clean_completion`. A later successful sweep can add `lineage_manifest_uncovered` only relative to a check that already froze the empty prefix; a receipt taken before any manifest does not carry that gap.
- **Suggested change and test:** In `evaluate_recorded_lineage` / `evaluate_lineage`, a missing aggregate (`source_event_id is None` and no manifest event) is not a clean empty child set. Emit a closed gap (the existing `lineage_manifest_uncovered` code, or a dedicated `lineage_manifest_missing`) that sets `blocks_clean_completion`. Keep a recorded empty `children` event as the clean “no children” case the coordinator already writes. `sweep_task` should surface `inventory_read_gap` to the maintenance caller instead of folding it into `None`. Test: parent ledger with a `delegation_declared` event and no `child_dependencies_recorded` event; `evaluate_recorded_lineage` blocks clean completion. Second test: recorded empty aggregate does not block. Third: `sweep_task` on a raising catalog returns a result whose gap flag is visible to the caller.
- **Confidence:** high
- **Related tests:** `tests/unit/kernel/test_lineage.py`, `tests/conformance/observation/test_lineage_receipt_acceptance.py`, `tests/unit/application/test_lineage_coordinator.py`. None of these lock “never swept” versus “swept empty”.

## L-02

- **Severity:** high
- **Category:** revocation not suppressing advice; coordination sweep abort
- **Title:** `detect` ignores sticky invalidation, and one refused pair aborts the rest of the project
- **File:lines:** `src/yoetz/application/coordination.py:1587-1600`, `src/yoetz/application/coordination.py:1955-1973`, `src/yoetz/application/coordination.py:2132-2157`, `src/yoetz/application/coordination.py:2159-2307`; maintenance swallow `src/yoetz/service/ready_composition.py:5849-5854` (caller, not the defect)
- **Evidence:** `CoordinationDetector._deliver` is the only writer of `generation_valid=False`. `redeliver` returns immediately when that bit is false, but `src/` never calls `redeliver`. The production sweep calls `CoordinationRuntime.detect`, which calls `detector.detect`. `detect` always calls `_deliver` on the stored row. `_deliver` does not read `generation_valid` before `record_context` or `put_advice`. `put_detection` returns the existing row unchanged, so a later `detect` cannot clear the bit and also does not refuse to deliver. Separately, `_admit_pair` raises `CoordinationError` before any invalidation write. `CoordinationRuntime.detect` does not catch that error, so the nested pair loop stops. `sweep_coordination` catches the exception per project and `continue`s, which drops every later pair in that project.
- **Failure scenario:** Tasks A, B, and C share a project. Pair A–B is delivered. Consent or grant for C then fails. The next sweep’s pair A–C raises inside `detect`. Pair B–C is never detected. The refused detection is not marked `generation_valid=False` unless the failure happened inside `_deliver` after `_admit_pair` had already succeeded. If that bit was set and the same generation later admits again (grant reactivated in place, or consent restored without a generation advance), the next `detect` calls `record_context` and `put_advice` anyway. Status reads in `coordination_advice_for` hide `generation_valid=False` rows, so the ledger can gain a context event that project status will not show. `assessments_for_check` (`coordination.py:1717-1753`) never reads `generation_valid` or the live grant; a context event already on the recipient ledger still becomes `COORDINATION_OVERLAP` after revocation. That check path is documented as frozen-case behavior; the sweep reopen is not.
- **Suggested change and test:** At the start of `_deliver`, return `()` when `generation_valid` is false. Catch `CoordinationError` per pair inside `CoordinationRuntime.detect`: on `GRANT_REVOKED`, `GENERATION_MISMATCH`, or `CONSENT_REQUIRED`, mark the existing detection invalid when one exists, record a refused delivery, and continue with the next pair. Do not let one pair’s admission failure discard the rest of the project. Test, extending `test_consent_refusal_irreversibly_invalidates_queued_detection`: after `generation_valid` is false, `detector.detect` with the same inputs returns `()` and does not call the context writer. Second test: three tasks, the middle pair’s admit raises, and the third pair still delivers.
- **Confidence:** high
- **Related tests:** `tests/conformance/observation/test_coordination_consent_generation.py` (`test_consent_refusal_irreversibly_invalidates_queued_detection` covers only `redeliver`), `tests/unit/application/test_project_coordination.py` (route rotation also asserts `redeliver`, not the sweep).

## L-03

- **Severity:** high
- **Category:** ambiguous auto-attach; project membership used as permission
- **Title:** Cross-repository delegation binds the first general project that lists both repositories
- **File:lines:** `src/yoetz/application/projects.py:2971-3041`
- **Evidence:** `admit_cross_repository_child` loads every project id, and for each general project loads every membership. It returns on the first project, in `str.encode` order, whose active repository members include both the parent repository and the child repository and whose current grant is active. It does not require the parent task to be a task member. It does not count other matches. The returned `project_id` and `membership_generation` are what `reserve_delegation` stores on the operation (`lineage.py:1328-1331`).
- **Failure scenario:** Two general projects both contain repository A and repository B and both have an active grant. A parent task in A delegates a child in B. The child is admitted under whichever project id sorts first, including a project the parent task never joined, and including a project the operator did not intend after revoking a different project that also linked the two repositories. Revoking only the project the operator thinks is in force leaves the other grant able to authorize the link.
- **Suggested change and test:** Collect every matching project. Zero matches keep today’s `CROSS_REPOSITORY_LINEAGE` refusal. More than one match raises a closed selector conflict (`auto_attach_binding_ambiguous` or `SELECTOR_CONFLICT`) with a count and no project id. Prefer an explicit project selector when the caller has one; do not infer from repository membership alone. Test: two general projects, both linking the same repository pair with active grants; `admit_cross_repository_child` fails and does not return the lexicographically first id. Existing single-project coverage in `tests/integration/service/test_cross_repository_lineage_grant.py` stays green.
- **Confidence:** high
- **Related tests:** `tests/integration/service/test_cross_repository_lineage_grant.py` (single project only).

## L-04

- **Severity:** high
- **Category:** project membership used as permission
- **Title:** Repository or workspace membership re-admits a task after that task is unlinked
- **File:lines:** `src/yoetz/application/projects.py:4076-4096`
- **Evidence:** `admit` sets `member` from `list_task_project_ids`. If the task is absent, a repository project with `auto_grouping` sets `member` from repository-commitment equality and ignores the membership rows it just loaded. A general project sets `member` when any active membership is the task’s repository or the task’s workspace. An inactive task row does not cancel that match. `item.active` applies to the coarse row, not to a prior task unlink.
- **Failure scenario:** A general project links repository R and later links task T in R. The operator unlinks T. `list_task_project_ids(T)` no longer returns the project, but the active repository membership still matches `provenance.repository_privacy_commitment`. `admit` succeeds. T keeps receiving coordination advice and can pass `project_view_for` / `coordination_advice_for`. The same holds for an auto-grouped repository project: every task whose repository commitment equals the project is admitted whether or not a membership row exists.
- **Suggested change and test:** Task admission is an active task membership, or a coarse repository/workspace membership only when no inactive task membership for that same task exists. Auto-grouped repository admission should still require the repository project’s own membership (or an explicit opt-in row), not commitment equality alone. Test: link repository and task, unlink the task, `admit` for that task raises `PROJECT_NOT_FOUND` while the repository row remains. Test: auto-grouped repository project does not admit a task that was explicitly unbound.
- **Confidence:** high
- **Related tests:** `tests/unit/application/test_project_coordination.py`, `tests/conformance/adapters/test_project_catalog_memberships.py`. No test unlinks a task while a repository member remains.

## L-05

- **Severity:** medium
- **Category:** stale generation accepted
- **Title:** Child manifest stores the maximum project generation, not the admitting one
- **File:lines:** `src/yoetz/application/lineage_coordinator.py:903-924`
- **Evidence:** `_membership_generation` lists every project id for the child and returns `max(generations)`. The comment says this must not become lineage authority. The value is still written onto `WireChildDependencySnapshot.membership_generation` (`lineage_coordinator.py:900`) and round-tripped by `ChildDependencySnapshot` (`kernel/lineage.py:308`, `kernel/lineage.py:474`). The cross-repository operation stores a different, specific generation on `DelegationOperation.membership_generation` (`lineage.py:1328-1331`). The two numbers are not required to match. `authorize_recorded_lineage` compares route generation, not this field, so the bad stamp is not currently a read gate. It is still the generation a later gate would treat as the child’s project generation.
- **Failure scenario:** Child is in project A at generation 2 (the grant that admitted the cross-repo link) and project B at generation 40. The manifest records 40. A check that treats “manifest generation equals current generation of the admitting project” sees a mismatch and fails closed, or a check that treats a greater number as newer accepts 40 as proof that generation 2 is still current after A was revoked and left at a lower number.
- **Suggested change and test:** Stamp the generation of the single project that admitted the child (the operation’s `project_id` / `membership_generation` when present). If the child is in several projects and none is the admitting project, stamp `None`. Do not take `max`. Test: two projects with generations 2 and 40; the snapshot’s `membership_generation` is 2 when the delegation operation names the generation-2 project, and `None` when no admitting project is known.
- **Confidence:** medium. The stamp is wrong. No in-tree reader currently authorizes on it.
- **Related tests:** `tests/unit/application/test_lineage_coordinator.py`. No multi-project generation assertion.

## L-06

- **Severity:** medium
- **Category:** write-off / cancel
- **Title:** Parent write-off or cancel leaves the child session active and discards a later close
- **File:lines:** `src/yoetz/application/lineage.py:2054-2094`, `src/yoetz/application/lineage.py:2179-2189`, `src/yoetz/application/lineage.py:2331-2414`; cross-check `src/yoetz/kernel/lineage.py:811-826`
- **Evidence:** `validate_child_work_transition` allows `CANCELLED` or `WRITTEN_OFF` for any direct child whose work is `OPEN`. It does not look at acceptance, at whether the child already has a receipt, or at the parent’s own work state. `_transition_work_locked` updates `work_state` and revokes attach handles. It does not end `session_health` or clear a child `active_session_id` (contrast `end_session`, which does). The idempotent path returns when `work_state is target` before `revoke_handles`; a crash between `save_task` and `revoke_handles` is repaired on attach by the `OPEN` check, not by a retry of revoke. The kernel classifies an accepted child in `WRITTEN_OFF`, `CANCELLED`, or `ABANDONED` as `INCOMPLETE` before the clean branch, even when `child_receipt_id` is set.
- **Failure scenario:** Child records a receipt while work is still `OPEN`. Parent publishes `child_written_off`. Catalog work becomes `WRITTEN_OFF`; the child session stays `ACTIVE`. The child’s `close_work` fails because work is not `OPEN`. The next manifest sweep copies the receipt and the written-off state. Rollup is `INCOMPLETE`, so the child’s completed receipt cannot make the dependency clean, and the child cannot record its own close.
- **Suggested change and test:** Before a parent terminal transition, sweep the child manifest (or refuse when the child ledger already has a receipt / `CLOSED` is the only honest terminal). On `WRITTEN_OFF` and `CANCELLED`, end the child session in the same locked update as the work transition, and call `revoke_handles` on the idempotent path when any handle for that task is still unrevoked. Test: child with a receipt and `OPEN` work; parent write-off either is refused, or ends the session and the rollup still surfaces the receipt as a known outcome rather than only `INCOMPLETE`. Test: retry of write-off revokes a handle left unrevoked by a crash.
- **Confidence:** high on the transition behavior; medium that product intent wants write-off to override a receipt (the parent is blocked either way, so this is not a clean-completion bypass).
- **Related tests:** `tests/unit/application/test_lineage_recovery_policy.py`, `tests/unit/kernel/test_lineage.py`, `tests/conformance/observation/test_parent_lineage_workflow.py`.

## L-07

- **Severity:** medium
- **Category:** parent / child session authorization
- **Title:** `end_session` ends whichever session is current, not the session named by the caller
- **File:lines:** `src/yoetz/application/lineage.py:2506-2534`; session map retention `src/yoetz/application/lineage.py:727-735`
- **Evidence:** `save_task` records `active_session_id → task` and never removes the previous session id. `mark_contact_lost` refuses unless `active_session_id == session`. `end_session` looks up the task by the given session id and, if health is not already `ENDED`, sets `session_health=ENDED` and `active_session_id=None` without comparing `snapshot.active_session_id` to the argument. The only in-repo caller is `tests/unit/application/test_lineage_recovery_policy.py`.
- **Failure scenario:** Task rotates from session A to session B (`bind_session`). A caller ends A. `get_session_task(A)` still returns the task. The method clears B’s active session and marks the task ended. B can no longer publish under an active lineage session. Recovery that keys off the ended flag then treats the live session as finished.
- **Suggested change and test:** Require `snapshot.active_session_id == session` before mutating, matching `mark_contact_lost`. Ending a historical session is a no-op success or `lineage_session_not_active`. Test: bind session B, `end_session(A)` leaves B active.
- **Confidence:** high on the code; low on current production exposure because no service caller was found.
- **Related tests:** `tests/unit/application/test_lineage_recovery_policy.py`.

## L-08

- **Severity:** medium
- **Category:** ambiguous auto-attach
- **Title:** Exact pair resume is rejected when more than one predecessor id is present
- **File:lines:** `src/yoetz/application/lineage.py:2667-2704`
- **Evidence:** `decide_admission` raises `ambiguous_binding` when `len(predecessor_task_ids) > 1` before it honors `same_pair_task_id`. A single predecessor returns `ATTACH` with that task id and `predecessor_resumed=True`, with no attach handle. The method’s own docstring says membership must not select a task. No production caller and no unit test reference `decide_admission` or `AdmissionDecision`; hook auto-attach is implemented separately in `src/yoetz/cli/observe_hooks.py` and was not re-audited here.
- **Failure scenario:** If a caller adopts this table, a start that already has a same-pair task id is refused whenever the predecessor list has two ids, including predecessors that are not the pair. The unique-predecessor branch attaches that task without a bearer handle.
- **Suggested change and test:** Honor `same_pair_task_id` first and return `RESUME`. Raise `ambiguous_binding` only when there is no same-pair id and no attach handle and the predecessor list has more than one id. Do not return `ATTACH` for a predecessor id unless the caller has already proven that id is the single validated ended same-host selector; otherwise return a typed refusal rather than a task id. Test the three rows: same-pair plus two predecessors resumes; two predecessors and no pair raises with count 2 and no task id; one predecessor does not attach unless an explicit validated-predecessor flag is set.
- **Confidence:** high on the table; low on exploitability until something calls it.
- **Related tests:** none.

## L-09

- **Severity:** medium
- **Category:** performance of project scans
- **Title:** Status and maintenance re-read every member and every detection
- **File:lines:** `src/yoetz/application/projects.py:2997-3015`, `src/yoetz/application/projects.py:3078-3188`, `src/yoetz/application/projects.py:3247-3376`, `src/yoetz/application/projects.py:4207-4303`, `src/yoetz/application/projects.py:4390-4448`; `src/yoetz/application/project_projection.py:299-322`
- **Evidence:** `admit_cross_repository_child` calls `list_project_ids` and then `project_state` plus `project_memberships` for every project. `project_view_for` calls `_authorized_member_views`, which expands every active repository member through `list_repository_task_ids` and every workspace member through `list_workspace_task_ids`, then for each task loads provenance, `admit` (which may load memberships again), work state, sessions, and lineage. The same view then calls `project_detections_for`, which lists every detection and, per row, reloads participants, both provenances, and `admit` twice, then repeats a second pass in `_sources_current_at_generation`. `coordination_advice_for` repeats that detection scan. `revalidate_status_advice_sources` calls `coordination_advice_for` once per project id for the requester. Maintenance `sweep_coordination` lists every project and runs a full pair detect.
- **Failure scenario:** A repository project with auto-grouping, or a general project that links a repository, makes one status read admit every task that has ever used that repository. Each detection adds a constant number of catalog round-trips. The revalidation pass multiplies that by the requester’s project count. Cost grows with catalog size, not with the page the client asked for.
- **Suggested change and test:** Resolve candidate tasks with an indexed query (project id, active member, task id), not `list_repository_task_ids` of the whole repository. Page detections by project and generation instead of `list_detections` plus per-row admits. Cache the generation and grant once per status call; do not `admit` the same task twice in `project_detections_for` and again in `_sources_current_at_generation` unless a generation change was observed. For cross-repo admission, query projects that contain both repository commitments rather than scanning every project. Test with a catalog of many tasks in one repository and assert the status path does not call `task_source_provenance` once per unrelated task (fake catalog counter).
- **Confidence:** high that the scans are unbounded; medium on user-visible cost, which depends on catalog size.
- **Related tests:** `tests/unit/application/test_project_projection.py`, `tests/unit/application/test_project_coordination.py`. No large-catalog bound.

## Not reported

- Handle reuse across sessions, expiry, and revoked handles: refused in `_validate_attach_locked` / `_attach_locked`.
- Accepted child cannot be rejected later: `_transition_acceptance` allows only `PENDING` → target.
- `coordination_advice_for` and `project_detections_for` do re-admit at the current generation and drop `generation_valid=False` rows. The hole is the writer and the check path in L-02, not those readers.
- `assessments_for_check` requires the recipient’s own declaration and an open obligation before a finding. Advice-only context does not mint `COORDINATION_OVERLAP`. That part of the obligation gate holds.


---

# Semantic review and egress

Source notes: `05-semantic-egress.md`.

# Semantic egress and privacy audit

Read-only review of the listed application and service modules, plus the privacy composition and audit adapters those modules call. No source was modified, no service was started, and no network call was made.

## Executive summary

Four defects are supported by the code as written. The highest-impact one lets a silent tighten drop an enabled channel's byte or token ceiling to zero; egress then treats zero as "no ceiling" and effective-policy intersection keeps that zero, so a repository row can erase a tighter machine cap. A second defect records a successful local-model review when the local disclosure receipt fails to commit, leaving the privacy audit non-terminal. A third redispatches an already-answered provider attempt when the post-result ledger write fails or the task is cancelled. A fourth strands a human-approved local-model disclosure because local consume accepts only `reserved`, then classifies that audit failure as a retriable transport outage.

No code path was found that copies a secret into a semantic case after a never-send hit, that lets import publication admit a second batch under a spent grant, or that lets provider text become an attention token. Prompt text inside a review packet can still influence an advisory conclusion; the implemented fence is post-validation of shape, cited refs, and coverage, not a delimiter around packet bytes. That residual is the ADR-006 design, not a skipped check in these modules.

## Findings

### E-01

- **Severity:** high
- **Category:** security
- **Title:** A zero byte or token ceiling is unlimited at dispatch and a silent tightening in policy
- **File:lines:** `src/yoetz/application/egress.py:1173-1214`; `src/yoetz/application/privacy_policy.py:361-384`, `753-766`, `789-798`, `982-983`; composition that feeds egress: `src/yoetz/domain/privacy.py:773-774`
- **Evidence:** On an enabled `llm_inference` row, dispatch skips the ceiling when the limit is not strictly positive:

```1173:1186:src/yoetz/application/egress.py
        if llm.max_bytes > 0 and minimized.byte_count > llm.max_bytes:
            return await self._complete_semantic_predispatch(
                ...
            )
        if llm.max_tokens > 0 and minimized.token_count > llm.max_tokens:
```

  The authorization bound then becomes the prepared case size itself:

```1202:1214:src/yoetz/application/egress.py
        # Authorization ceilings bind policy ∩ case (never case size alone).
        auth_max_bytes = (
            minimized.byte_count if llm.max_bytes <= 0 else min(llm.max_bytes, minimized.byte_count)
        )
        auth_max_tokens = (
            1
            if candidate.purpose == _CREDENTIAL_PROBE_PURPOSE
            else (
                minimized.token_count
                if llm.max_tokens <= 0
                else min(llm.max_tokens, minimized.token_count)
            )
        )
```

  The comment says the ceiling is policy intersected with the case and never the case size alone. The `<= 0` branch does the opposite.

  The same zero is the required value of a *disabled* channel (`ChannelPolicy.__post_init__` rejects any positive limit while `enabled` is false). On an *enabled* channel, zero is legal and means unlimited.

  The tighten classifier treats a numeric decrease as a restriction:

```753:766:src/yoetz/application/privacy_policy.py
        "max_bytes",
        ...
        lambda new, old: new.max_bytes > old.max_bytes,
    ),
    (
        "max_tokens",
        ...
        lambda new, old: new.max_tokens > old.max_tokens,
```

  `4096 → 0` is therefore `widens=False`. `_is_tightening` is true when no change widens (`privacy_policy.py:982-983`). `privacy_propose_policy` then commits through `privacy_tighten_policy` or `policy_store.tighten` with no human decision (`privacy_policy.py:361-384`).

  Effective policy is `ChannelPolicy.meet`, which uses ordinary `min`:

```773:774:src/yoetz/domain/privacy.py
            min(self.max_bytes, other.max_bytes),
            min(self.max_tokens, other.max_tokens),
```

  `min(65536, 0)` is `0`. A repository "tighten" to zero replaces a positive machine cap with the unlimited sentinel before egress runs. The inverse is also wrong: `0 → 4096` is classified as a widening, so adding a real cap demands human approval.

  `authorization_ttl_seconds` is not the same bug. Egress floors a zero TTL with `max(60, llm.authorization_ttl_seconds or 60)` (`egress.py:1232-1233`), so zero is not a longer lifetime.

- **Failure scenario:** An enabled external review channel is capped at 4096 bytes. A later candidate changes only that field to 0. The proposal is classified as a tightening and commits without the approval ceremony. The next semantic check builds a case larger than 4096 bytes. Egress does not refuse it and authorizes `max_bytes` equal to the prepared size. If the machine row still has 65536 and the repository row is the one set to 0, `meet` yields 0 and the machine cap is gone as well.
- **Suggested fix and test:** Stop overloading 0. For an enabled channel, require `max_bytes` and `max_tokens` to be positive, or represent "no extra cap" with a distinct absent value that the classifier treats as a widening relative to any positive cap. `meet` must treat the unlimited sentinel as wider than every positive cap (`min` of a positive cap and unlimited is the positive cap). Egress must refuse an enabled row whose ceiling is the unlimited sentinel unless that sentinel was an approved widening, and must not substitute `minimized.byte_count` for a missing ceiling.

  Tests:
  - `privacy_policy_changes` on an enabled LLM row: `max_bytes` 4096 to 0 has `widens=True`; 0 to 4096 has `widens=False`. Same pair for `max_tokens`.
  - `privacy_tighten_policy` / `privacy_propose_policy` rejects 4096 to 0 with `privacy_authority_required` and does not call `policy_store.tighten`.
  - `ChannelPolicy.meet` of 65536 and 0 (once 0 is the unlimited sentinel) keeps 65536. Today `tests/unit/privacy/test_effective_policy_intersection.py` only locks positive caps.
  - Egress pipeline with an enabled row at `max_bytes=0` does not dispatch a prepared case larger than the last approved positive cap, and the minted authorization's `max_bytes` is not the full minimized size.
- **Confidence:** high
- **Related tests:** `tests/unit/application/test_privacy_policy_changes.py` (`max_bytes_raised` only covers 1024 to 2048); `tests/builders/privacy_widenings.py`; `tests/unit/privacy/test_effective_policy_intersection.py`; `tests/integration/privacy/test_egress_gateway.py` (admission uses a positive `max_bytes`). None of these lock the zero sentinel.

### E-02

- **Severity:** high
- **Category:** correctness
- **Title:** A failed local disclosure receipt still returns a successful review
- **File:lines:** `src/yoetz/application/egress.py:1416-1455`; audit transition the swallow depends on: `src/yoetz/adapters/privacy/catalog.py:2299-2339`
- **Evidence:** Local dispatch consumes the reservation before the model runs. `consume_local` moves `reserved` to `local_disclosure_pending` (`catalog.py:2308-2320`). After `dispatch_local_semantic` returns, egress writes the receipt and ignores every failure:

```1443:1455:src/yoetz/application/egress.py
            try:
                await self._audit.complete_local_disclosure(proposal.privacy_proposal_id, receipt)
            except Exception:
                pass
            return await self._map_provider_result(
                candidate.request_id,
                proposal.privacy_proposal_id,
                None,
                SemanticDispatchKind.LOCAL_MODEL,
                proposal.prepared_case_digest,
                subject_digest,
                result,
            )
```

  `complete_local_disclosure` only succeeds from `local_disclosure_pending` (`catalog.py:2335-2339`). A conflict, a full disk, or a mismatched receipt leaves that state in place. `_map_provider_result` still returns `SemanticEgressSuccess` when the model result is `SemanticResultSuccess` (`egress.py:1561-1570`). `run_durable_semantic_attempts` then publishes the response and `select_attempt`s it (`semantic_attempts.py:1341-1385`), which is the job's success terminal.

  External dispatch does not do this. The gateway parks an `outcome_unknown` receipt at consume time and completes the real receipt inside the gateway (`adapters/privacy/gateway.py:748-770`, `939`). A local success has no parked receipt and no second chance: `recover_started_attempt` would report `SemanticEgressAttemptUnknown` for `local_disclosure_pending` (`egress.py:466-476`), but that recovery does not run once the success result has already been returned and selected.

- **Failure scenario:** Baseline local-model review (no per-request preview, so the row is still `reserved`) admits the case, the local model returns a usable judgment, and `complete_local_disclosure` raises. The check commits that judgment as the selected attempt. The privacy catalog row stays `local_disclosure_pending`, with no `receipt_id`. `privacy receipts` cannot show the disclosure. A later recovery of the same request is `attempt_unknown`, which disagrees with the committed success.
- **Suggested fix and test:** Do not map a provider result until `complete_local_disclosure` has committed. On failure, return a terminal coordinator or receipt-persistence outcome and do not select the attempt. Mirror the external path: park a structural unknown receipt in the same transaction as `consume_local`, and let startup reconciliation finish `local_disclosure_pending` rows that outlived the dispatcher. Never `except Exception: pass` around the completion write.

  Tests:
  - Audit double whose `complete_local_disclosure` raises after a scripted local success: `evaluate_semantic` is not `SemanticEgressSuccess`, and the semantic job is not `succeeded`.
  - The audit row is either still unconsumed or durably receipted; it is not `local_disclosure_pending` beside a selected attempt.
  - A crash after consume and before complete reconciles to one terminal receipt and does not call the local evaluator again.
- **Confidence:** high
- **Related tests:** `tests/integration/privacy/test_egress_gateway.py` asserts `consume_local` was called; it does not fail the completion write. `tests/unit/application/test_semantic_local_egress.py` covers the happy path.

### E-03

- **Severity:** medium
- **Category:** correctness
- **Title:** A post-result ledger failure or cancellation redispatches the same attempt
- **File:lines:** `src/yoetz/application/semantic_attempts.py:1307-1331` and `1336-1529`
- **Evidence:** The claim-to-dispatch `try` terminalizes on any failure so a `started` attempt cannot be resumed into another provider call (`semantic_attempts.py:1307-1331`). The following `try`, which runs only after `attempt_dispatch` has returned, logs and re-raises:

```1522:1529:src/yoetz/application/semantic_attempts.py
        except BaseException as exc:
            record_unexpected_exception_without_raising(
                exc,
                component="semantic_attempts",
                operation=f"semantic_attempt_{final_stage}_failed",
                request_id=current_lease.operation_id,
            )
            raise
```

  That frame is where a non-success outcome is stored as `EXPIRED` or `FAILED` (`semantic_attempts.py:1482-1516`) and where a success is `select_attempt`ed after `RESPONSE_DURABLE` (`1372-1379`). Response persistence has its own terminalizer (`1345-1371`). The outcome write and `select_attempt` do not. `claim_semantic_job` resumes a `started` row. The resume branch skips dispatch only when the row is already `response_durable` (`1192-1231`). A failed or cancelled outcome write leaves `started`, so the next claim sends the same frozen case again.

  Cancellation is the same hole. The claim-phase handler calls `_terminalize_cancellation_safe` before re-raising `CancelledError` (`1312-1322`). The result-phase handler does not, so cancel-after-answer also leaves `started`.

- **Failure scenario:** The provider returns `invalid` or `unavailable`. `record_attempt_outcome` raises (lock, disk, or a cancelled task during the write). The operation fails out of the attempt loop with the attempt still `started` and the job still `leased`. Recovery claims that attempt, does not see `response_durable`, and calls the provider again with a new physical dispatch of the same case. Repeating the write failure repeats the send until the deadline or an operator stops the replay. A success that already reached `RESPONSE_DURABLE` does not resend; the hole is every outcome that has not yet been durably closed.
- **Suggested fix and test:** Use the same terminalizer as the claim-phase handler for every exception after dispatch returns, including `CancelledError`. If the provider result is already in hand, record that outcome (or a single `outcome_unknown` / coordinator failure) before propagating. Do not resume a `started` attempt that this process has already dispatched; a resumed `started` row whose dispatch entered the provider is `outcome_unknown`, not a fresh send.

  Tests:
  - Ledger whose `record_attempt_outcome` raises once after a scripted non-success: the job is terminal, and a second `run_durable_semantic_attempts` performs zero provider calls.
  - Cancellation after dispatch and before the outcome write: one provider call, then a terminal attempt row, and replay does not call the provider.
  - Existing `response_durable` recovery test stays green (no second send).
- **Confidence:** high
- **Related tests:** `tests/unit/application/test_semantic_attempts.py`, `tests/unit/application/test_semantic_attempts_fallback.py`. They lock the retry matrix and the claim-phase terminalizer; they do not fail the post-result write.

### E-04

- **Severity:** medium
- **Category:** correctness
- **Title:** A human-approved local-model disclosure cannot be consumed, and the failure is a retriable transport error
- **File:lines:** `src/yoetz/application/egress.py:1317-1348` and `1391-1455`; `src/yoetz/adapters/privacy/catalog.py:2304-2320` and `2806`; mapping that renames the failure: `src/yoetz/service/ready_composition.py:2951-2977`; retry admission: `src/yoetz/application/semantic_attempts.py:387-402` and `71-76`
- **Evidence:** External authorize accepts both pre-approval and post-approval rows:

```2806:2806:src/yoetz/adapters/privacy/catalog.py
                           WHERE proposal_id = ? AND state IN ('reserved', 'approved')
```

  Local consume accepts only `reserved`:

```2308:2309:src/yoetz/adapters/privacy/catalog.py
                if row is None or row[0] != "reserved":
                    raise ValueError("privacy_local_reservation_unavailable")
```

  Human approval moves the row to `approved` (`catalog.py:2682-2702`) and egress then calls `_dispatch_approved` (`egress.py:1339-1348`). The local branch always calls `dispatch_local_semantic` (`egress.py:1416-1417`), which calls `consume_local` and turns any exception into `SemanticResultUnavailable` with `failure_class=unsupported_profile` (`gateway.py:1072-1075`, `1264-1286`). The model is not called. `complete_local_disclosure` then fails because the state is `approved`, not `local_disclosure_pending`, and that exception is swallowed (E-02). The mapped public reason is `transport_unavailable` (`ready_composition.py:2965-2977`), which is in `_RETRIABLE_REASONS`. `unsupported_profile` is not in `_NON_RETRIABLE_FAILURE_CLASSES` (only authentication and authorization). The attempt loop therefore spends the transient retry budget on a reservation that can never become consumable. Fallback does not turn this into external egress: a fallback binding is rejected unless the primary transport is already external (`domain/privacy.py:685-690`).

- **Failure scenario:** Local model is enabled and the channel or profile requires a per-request preview. The human approves the exact case. Every dispatch fails consume, the check ends `unavailable` / `retry_budget_exhausted` or `transport_unavailable`, and the audit row remains `approved`. The same approval cannot be resumed into a local evaluation. Baseline local review (row still `reserved`) is unaffected.
- **Suggested fix and test:** `consume_local` must accept `approved` as well as `reserved`, and must keep the consent source already stored on an approval instead of writing `baseline_policy` (`catalog.py:2318`). If consume fails, egress must return `SemanticEgressBlocked` with `audit_failed`, not a provider unavailable result, and must not retry it. Do not swallow the follow-up receipt error.

  Tests:
  - Preview-required local pipeline: after a scripted human approval, one local evaluation runs and the audit ends `local_disclosure_completed` with the approval's consent source.
  - A consume failure is not `SemanticReason.TRANSPORT_UNAVAILABLE` and `should_retry_after` is false.
- **Confidence:** high
- **Related tests:** `tests/unit/application/test_semantic_local_egress.py`; `tests/unit/application/test_semantic_authorization_resume.py` (external `approved` resume). No test approves a local-model proposal and then dispatches it.

### E-05

- **Severity:** low
- **Category:** security
- **Title:** The package-update cache file is not a private, symlink-free state file
- **File:lines:** `src/yoetz/application/package_update.py:206-218` and `251-287`
- **Evidence:** `load_package_update_cache` uses `Path.read_bytes` and returns whatever JSON matches the schema. It does not check owner, mode, or symlink. `store_package_update_cache` calls `ensure_owner_only_dir` on the parent, then `write_bytes` (which follows a symlink) and `chmod`. The recommendation store next to it rejects a symlink, a foreign uid, and any mode bit outside owner read/write (`recommendations.py:469-476` and `500-508`, with `O_NOFOLLOW` on the lock). The cache body is only a version string, not case content. Policy still gates the network: `resolve_package_update_advisory` returns `skipped_policy` before touching the cache when update checks are not permitted (`package_update.py:304-318`). A fresh permitted cache is not a second network call.
- **Failure scenario:** A symlink at `package-update-cache.json` inside the state directory is followed on read and on write. A read can surface a version string that did not come from the PyPI response this process stored. A write can replace the target of that symlink with the cache document. Same-user only; the parent directory is owner-only.
- **Suggested fix and test:** Open the cache with `O_NOFOLLOW`, require a regular file owned by the current uid and mode `0600`, and replace via a temporary file in that directory the way `store_recommendation_state` does. Fail closed to "no cache" on any of those checks.

  Test: a symlink at the cache path is not read and is not written through; a mode `0644` file is not loaded.
- **Confidence:** medium
- **Related tests:** `tests/unit/application/test_package_update.py` (policy skip, TTL, transport failure). It does not lock file identity. `tests/unit/application/test_recommendations.py` is the pattern to copy.

## Checked, not reported

- **Captured `redacted` bytes in the case.** `test_redacted_captured_bytes_remain_available_with_explicit_gap` and ADR-020 require sanitized redacted bytes to stay in the excerpt with a `content_redacted` gap. Evidence rows with `redacted=True` omit payload. This is not a secret leak.
- **Never-send at the case boundary.** `LocalPrivacyEnforcer.classify` scans each candidate item, marks hits `SECRET_OR_CRYPTOGRAPHIC`, and `PrivacyCoordinator._semantic_decision` blocks the whole case on any forbidden finding (`egress.py:1631-1637`). `minimize_and_scan` scans the assembled packet again. No scoped path reinserts a blocked item.
- **Import publication grant.** `ImportPublicationAuthority.__call__` admits one exact request, writer, session, and event-id tuple, then sets `admission_used`. `activate` without a matching authorization always raises `PRIVACY_AUTHORITY_REQUIRED`. `bind` is one batch at a time from `execute_import_codex_jsonl`, which stops on the importer's plan.
- **Recommendation accept/decline.** `record_recommendation_decision` does not itself require a pending row, but `yoetz recommend accept` refreshes and refuses a non-pending id before apply. Decline goes through `decline_cached_recommendation`, which requires the cached pending id and does not re-enter network or context evaluation. Package-update accept does not run an upgrade; it prints `yoetz upgrade`.
- **Update-check network gate.** With a `PrivacyPolicy`, both `network_egress_permitted` and an enabled `update_checks` row are required. Boolean-only callers fail closed unless both bits are true. Disabling the channel does not serve the cache.
- **Prompt injection changing a verdict.** Packet item text is canonical JSON in the user message. `SEMANTIC_REVIEW_INSTRUCTION` tells the model not to waive findings or invent refs. Post-validation drops challenges whose `cited_refs` are outside `citable_refs`. There is no additional delimiter fence around item text. ADR-006 defines the fence as that post-validation, not as a content wrapper. An advisory `no_material_discrepancy` can still follow text inside the packet; that is the model risk the receipt labels as advisory, not a bypass of the ref fence.
- **Semantic attention.** `semantic_attention_for_outcome` reads only the closed failure class and runtime stage. Provider text is not copied into the token. The tracker is in-memory per ready generation.
- **Upgrade plan and maintenance.** `build_upgrade_plan` returns inspection and preview commands and states that upgrading does not opt into expanded review. Maintenance execute requires `explicitly_accepted` and an equal `plan_digest`. Recovery secrets are rejected if passed as raw `str` or `bytes`.
- **Retry matrix.** Transient reasons are timeout, transport, and rate limit, capped by ADR-006 (`max_retries` ≤ 2). Repair is one `response_content_invalid` per job, counted from durable rows. Quota is not retried on the same endpoint. Content-shaped answers do not license fallback. The duplicate send in E-03 is outside that matrix: it is a replay of an unclosed `started` row.


---

# Service daemon and control channel

Source notes: `06-service-daemon.md`.

# Service daemon audit (read-only)

Scope: `src/yoetz/service/daemon.py`, `client.py`, `confidential_client.py`, `confidential_protocol.py`, `control_protocol.py`, `ready_composition.py` (dispatch/gate touchpoints), `lifecycle.py`, `loop_health.py`, `check_waits.py`, `human_control.py`, `secret_ingress.py`, `project_coordination_authority.py`, `src/yoetz/adapters/control/unix_socket.py`, and the service tests that cover those paths. No source was modified. The service was not started.

Threat-model note used while judging: ADR-008 and `docs/protocol/local-service-security.md` state that peer-UID checks and mode `0700`/`0600` endpoints stop other local users, and that a malicious process already running as the same account is outside that boundary. Findings below are defects inside the implemented contracts (slot accounting, ceremony deadlines, flock-vs-stamp). Same-UID socket replacement is recorded only where the code’s own fence does not match its comments.

## Executive summary

Three defects are worth fixing. The secret-ingress listener leaks one of its 128 accept slots every time a ceremony cancels or times out while `accept()` is blocked, so passphrase and provider-credential entry stops working after enough abandoned prompts until the process restarts. Human-control reads have no deadline, and an installation-recovery prepare holds both ordinary dispatch gates until that read finishes, so a live but silent peer stalls maintenance and any ordinary call that omitted `deadline_ms`. Supersede sends `SIGTERM` to the pid stamped in the lock file and never checks that the flock is held, which `ServiceLifecycle` says is required because a pid can be reused.

Ordinary control framing, peer-UID checks, socket symlink checks, and in-flight RPC caps behaved as written. No cross-user socket authentication bypass showed up in this pass.

## Findings

### D-01

- Severity: high
- Category: missing cleanup / availability (accept-slot leak)
- Title: Cancelling `UnixEndpointListener.accept` leaks the connection slot
- File: `src/yoetz/adapters/control/unix_socket.py:245-266`; trigger in `src/yoetz/service/secret_ingress.py:171-180` and `188-199`
- Confidence: high

Evidence: `accept` acquires `_slots` (capacity `_MAX_ACTIVE_CONNECTIONS`, 128) before `sock_accept`. `LocalControlTransportError` and `OSError` release the semaphore. `asyncio.CancelledError` is not caught, so a cancel while blocked in `sock_accept` drops the slot and does not close a partially accepted socket.

`SecretIngressService.accept_once` is the production caller that cancels that wait. On `CEREMONY_EXPIRY_SECONDS` (300s) it cancels the task. `cancel_pending` does the same when the human peer aborts a `secret_required` phase. Both happen while `listener.accept()` is still blocked if nobody has connected to `secret-ingress.sock` yet. The slot is transferred to `AuthenticatedUnixStream` only after accept returns; the stream’s `aclose` is what releases it. The cancel path never gets that far.

`tests/integration/service/test_secret_ingress.py::test_cancel_pending_closes_connection_and_returns_bounded_cancel` injects a fake listener, so it never runs `UnixEndpointListener.accept`. `tests/integration/service/test_local_control_channel.py` covers mode, symlink, inode, and stale removal, not cancellation.

Failure scenario: Each abandoned secret prompt (user closes the terminal, or the 300s ceremony window elapses before the helper connects) cancels one in-flight `accept` and permanently consumes one slot. After 128 such cancellations the semaphore stays at zero. The next `accept_once` blocks in `acquire()` until its own 300s timeout and then fails `binding_expired`, without ever reading a secret. Passphrase unlock, passphrase init, provider-credential entry, and installation-recovery secrets all stop until the daemon process exits. No second user is required.

Suggested fix: In `accept`, treat `CancelledError` like the other failure paths: if a socket was already returned, close it; always `release()` the semaphore if the slot was not handed to an `AuthenticatedUnixStream`; then reraise. Do not release twice when the stream was returned (the stream owns the slot). Add a regression that binds a real `UnixEndpointListener`, cancels `accept()` 128 times with no client, then connects once and asserts `accept()` completes. Also cancel an accept that is blocked in `sock_accept` after a client is sitting in the backlog and assert the accepted fd is closed and the slot count returns to 128.

### D-02

- Severity: medium
- Category: missing timeout / lock held across an unbounded wait
- Title: Human-control reads never time out, and installation recovery holds both dispatch gates for that whole wait
- File: `src/yoetz/service/daemon.py:3836-3857`, `4320-4346`, `3066-3131`, `3409-3411`, `1110-1113`
- Confidence: high

Evidence: `_read_human_envelope` / `_read_stream_exact` read the YZH1 header and payload with no `asyncio.timeout`. `_HumanConnectionServer` uses that read for the opening frame and for every non-secret phase (`decision_required`, `authorization_required`, `keyring_retry`). Expiry is checked only when a frame arrives (`HumanControlService._check_live`). A peer that keeps the socket open and sends nothing never hits that check. Secret phases are bounded because `accept_once` uses `asyncio.timeout(CEREMONY_EXPIRY_SECONDS)`. The other phases are not.

For installation recovery other than restore, `_LockedHumanEffects.prepare` calls `_acquire_recovery_maintenance` and returns the preview without releasing the gates. `observation_gate` and `maintenance_gate` stay held until `complete_installation_provision`, `revoke_installation_recovery`, or `cancel_installation_recovery`. The daemon comment at `1110-1113` says this wait is unbounded and that only the caller’s `deadline_ms` limits ordinary calls. `deadline_ms` is optional on `ControlCallRequest`. CLI project entry points default it to `None`, and `_acquire_dispatch_gate` then waits on the lock with no timeout.

The ordinary control listener does the opposite: handshake is capped at 5s and an idle session at 300s (`daemon.py:217-220`). The human listener’s accept loop does not wrap the handler in either budget, so a connected peer that drips a partial frame also holds one of the 128 human-control slots until the process exits. One live ceremony is global (`HumanControlService.open_ceremony` rejects a second with `state_forbidden`).

Failure scenario: A human starts installation recovery (or any non-secret ceremony) and the foreground helper stays connected without sending the next action. Recovery holds both gates, so observation sweeps, lineage recovery, and coordination sweeps block inside `async with maintenance_gate`. An ordinary call with no deadline blocks in `_acquire_dispatch_gate` until the ceremony ends. The idle monitor does not stop the process, because `client_connected` cleared the idle clock for that socket. Closing the helper cancels the ceremony and releases the gates; leaving it open does not. A separate pile of 128 partial human-control connections fills the human listener, and the legitimate ceremony then blocks in `accept` before it can even open.

Suggested fix: Put the same class of budget the control plane already uses on every human read: a short cap for the first frame, and `CEREMONY_EXPIRY_SECONDS` (or the binding’s `expires_at_monotonic_ms`) for later frames. On timeout, cancel the ceremony so `finally` in `_HumanConnectionServer` runs. For recovery, do not hold `observation_gate` / `maintenance_gate` across the human wait. Hold them only around snapshot staging and around the commit section; drop them before returning the preview, and take them again in `complete_*` / `revoke_*`. Add a test that opens an installation-recovery ceremony, writes nothing further, and asserts that within the ceremony budget the gates are free and a `service_status` plus a deadline-less no-op read are not stuck. Add a second test that a partial human frame older than the handshake budget drops the connection and a later ceremony can still `accept`.

### D-03

- Severity: medium
- Category: singleton lock race (stamp treated as the fence)
- Title: Supersede signals the stamped pid without checking the flock
- File: `src/yoetz/service/client.py:1491-1521`; contrast `src/yoetz/service/lifecycle.py:644-655`, `718-728`, `780-808`, and `src/yoetz/service/client.py:1622-1633`; acquire path `src/yoetz/service/lifecycle.py:323-336`
- Confidence: high on the missing check; medium on how often a wrong pid is live

Evidence: `probe_singleton_holder_identity` documents that `kill(pid, 0)` cannot tell pid reuse from the original holder, that the stamp is not a fence, and that startup decisions must corroborate with a lock probe. `probe_singleton_holder_lock` is that probe. The hook arm of `connect_service_on_demand` uses it (`1627-1633`) and discards a stamp when the flock is not held.

`supersede_incompatible_service` does not. It loads the stamp, compares manifest and version, then `os.kill(holder.pid, signal.SIGTERM)`. `wait_for_singleton_release` then polls the stamp, not the flock, and treats “pid changed or stamp unreadable” as release.

`acquire_singleton` opens the lock with `O_RDWR | O_CREAT | O_CLOEXEC` and `flock(LOCK_EX | LOCK_NB)`. It does not pass `O_NOFOLLOW` (the probe and the CLI instance stopper do). `_assert_singleton_descriptor_held` only `fstat`s the held descriptor. It does not check that the path still names that inode. Replacing the path (unlink, new file or symlink) leaves the daemon holding the old inode while readers of the path see the new file. The daemon still believes it holds the singleton.

`tests/unit/service/test_client.py::test_supersede_signals_only_a_live_foreign_identity_holder` stamps a `sleep` pid into a regular file and expects `SIGTERM`. The sleeper does not hold the flock. That test locks in the unsafe behavior.

Failure scenario: The stamp and the flock diverge. Two ways that happen with code as written: the holder crashes and the kernel drops the flock but the file body still contains the old pid, and that pid is later reused; or a same-account process replaces the lock path after acquire, because the directory is owner-writable and the acquirer never pins the inode. Supersede then signals whatever pid is in the file the path now names. `wait_for_singleton_release` can return true as soon as that pid disappears, and `connect_service_on_demand` will spawn a successor. The process that still holds the original inode is not the one that was signalled. `cli/instance.py` (`_stop_owned_service`) already refuses to signal unless `_lock_held` is true; the service client does not.

This is not a cross-user break. The state directory is created owner-only (`_ProductionPaths.canonical`). It is a same-account pid signal and a singleton identity split, which the lifecycle module says the flock exists to prevent.

Suggested fix: Before `os.kill`, require `probe_singleton_holder_lock(path) is True`. After the signal, wait until that probe returns false, not only until the stamped pid changes. In `acquire_singleton`, open with `O_NOFOLLOW`, and after `flock` compare `os.fstat(descriptor)` with `os.lstat(path)` (device, inode, owner, mode, `nlink == 1`). Fail the acquire if they differ. Teach `_assert_singleton_descriptor_held` the same comparison so endpoint recovery cannot run against a replaced path. Replace the unit test that signals an unflocked sleeper with one that refuses to signal when the flock is free, and one that signals only when the probe and the stamp agree.

## Checked, not filed

- Other-user socket access. `unix_socket.py` checks runtime-dir mode `0700`, socket mode `0600`, owner, `nlink == 1`, `S_ISSOCK`, and rejects symlinks via `lstat`. Connect rechecks the inode after `connect`. Linux peers use `SO_PEERCRED`; macOS uses `getpeereid`; UID mismatch is `peer_untrusted`. Accept runs only after `listen`, which runs after `chmod(0600)`. Close unlinks only the bound inode (`test_close_never_unlinks_a_replaced_endpoint`).
- Same-account socket replacement. A process of the same uid can unlink `control.sock` and bind its own. Clients only check uid, not the singleton pid. ADR-008 and the local-service security page say that principal is outside the threat model. Not filed as an authorization bypass.
- Frame bounds. Control rejects a declared length above `MAX_CONTROL_FRAME_BYTES` (6,291,456) before reading the body (`control_protocol.py:536-543`). Ordinary frames over 1,048,576 bytes are rejected unless they are a bounded import. Human frames cap the payload at 65,536 before the body read (`daemon.py:4323-4329`, `confidential_protocol.py:1334-1337`). Secret binding and secret lengths are checked before `read_exact` (`secret_ingress.py:250-277`). No unbounded read of a declared length.
- In-flight caps. `ControlSession.admit` caps a connection at 32 calls. `BoundedControlQueue` is capped but has no production constructor. `CheckWaits` caps detached checks at 8. Listener admission is 128, aside from D-01.
- Control deadlines. Handshake 5s, idle session 300s, response write 300s. Client connect/handshake is budgeted in `_connect_service_attempt`.
- Secret handling on the YZS1 path. The secret is a `bytearray`, validated as a view, captured (copied) by `LocalSecretMemory.capture`, then wiped in `finally`. The ingress socket writes no response. Replay of a challenge is recorded before the read; a live binding must still match byte for byte, and challenges are 32-byte tokens minted per phase, so the 4096-entry replay window expiring does not by itself revive a live ceremony.
- `project_coordination_authority.py`. `authorize` returns false until an exact owner-only artifact exists. A different pending slot is not replaced. `consume` runs only after the catalog write and treats a missing artifact as already reconciled. No second grant path that accepts a caller-supplied authority label.
- `loop_health.py`. The sampler thread writes the diagnostics ring and does not take the stderr logging lock. No daemon deadlock found there.
- `check_waits.py` window. `admits_readers` and `enter` run with no await between them. `close` waits for readers before the check releases the gates. Operation-status reads do not take those gates again. No self-deadlock found.
- Activation failure after `READY`. `_activate_ready_application_locked` locks the vault on any failure and moves `UNLOCKING` to `LOCKED`, but if the failure happens after `transition(READY)` it leaves lifecycle state `READY` with no application (`daemon.py:830-856`). The only throw after that transition in this function is `_start_ready_maintenance` seeing a task that this same lock already started. No production path was shown that hits it, so it is not numbered.

## Related tests

- `tests/integration/service/test_local_control_channel.py` — endpoint mode, peer uid, stale removal, inode-stable close.
- `tests/integration/service/test_secret_ingress.py` — binding match, expiry, cancel; fake listener, so D-01 is untested.
- `tests/unit/service/test_client.py::test_supersede_signals_only_a_live_foreign_identity_holder` — asserts D-03’s current behavior.
- `tests/integration/service/test_daemon_clients.py` — dispatch, projection failure, accept-loop arming. No human-read deadline test.


---

# Vault, unlock, recovery, and upgrades

Source notes: `07-vault-upgrade.md`.

# Vault, unlock, recovery, and bundle upgrade audit

Read-only review of the passphrase vault, OS-keyring adapter, installation recovery, elevated consent, and bundle-upgrade path. No service was started and no live state directory was opened.

Scope: `src/yoetz/service/vault.py`, `unlock.py`, `elevated_bootstrap.py`, `installation_recovery.py`, `bundle_upgrade.py`, `bundle_upgrade_effects.py`, `src/yoetz/adapters/keys/`, `src/yoetz/cli/unlock.py`, `elevated.py`, `upgrade.py`, and the marker switch those modules call in `src/yoetz/service/daemon.py` (`replace_after_recovery`, `replace_passphrase_envelope`, `replace_after_root_rotation`).

Threat model used for severity: ADR-004 and ADR-008. Other local users, stolen disks, and at-rest copies are in scope. A compromised logged-in UID, root, and inspection of a ready service's memory are out of scope. The coding agent is outside the vault boundary, so a same-UID process that can already rewrite the data directory is not treated as an authentication break. It is treated as in scope when a ceremony claims to have verified bytes that a later step installs without checking them again.

## Findings

### V-01

- Severity: medium
- Category: security
- Title: Snapshot install copies members without re-checking their digests
- File: `src/yoetz/service/installation_recovery.py:883-926` and `:1607-1647`
- Evidence: `import_archive` verifies each member with `_verify_prepared_snapshot` (`:866`), which hashes files through `_file_digest` (`O_NOFOLLOW`, mode `0600`, `nlink == 1`). `install_snapshot_into_pristine` does not call that verifier. It calls `_snapshot_facts`, then copies every path for which `Path.is_file()` is true. `_snapshot_facts` adds the sizes declared in the manifest (`observed_total += size`) and compares that sum to the manifest's own `total_bytes`. It returns `sha256` of the manifest bytes. It never reads member contents, so a replaced file with the same logical name still passes. `Path.is_file()` follows symlinks; the subsequent `_copy_private_file` uses `O_NOFOLLOW` and will fail closed on a symlink, but a regular-file substitution is copied.
- Failure scenario: Import of a self-contained set succeeds and records the manifest digest. Before `yoetz service recovery restore` installs that set into a pristine profile, a process that can write the staged set (the data directory is the user's) replaces `snapshot/members/catalog.sqlite3` or a task bundle and leaves `manifest.json` and the vault files unchanged. Install accepts the manifest digest and name set, renames the stage over the profile, and the later recovery ceremony unlocks the untouched vault. The restored catalog and bundles are no longer the bytes the import verified.
- Suggested fix and test: At the start of `install_snapshot_into_pristine`, run the same per-member digest, size, mode, and symlink checks as `_verify_prepared_snapshot` before creating the stage, and again on the staged tree immediately before the first `os.rename`. Compare actual file sizes, not the sizes stored in the manifest. Select members with `lstat` and `S_ISREG`, matching `_bundle_files`. Test: import a fixture archive, overwrite one member with a same-name regular file of different bytes, call `install_snapshot_into_pristine`, and assert `installation_snapshot_manifest_invalid` (or the member-invalid reason) with the original profile still in place. A second test plants a symlink member and asserts the install fails before either rename.
- Confidence: high
- Related tests: `tests/integration/service/test_installation_recovery_flow.py`, `tests/integration/objects/test_installation_recovery.py`, `tests/unit/service/test_installation_recovery_store.py`, `tests/unit/cli/test_installation_recovery_cli.py`. None of these lock install-time member digests after a post-import substitution.

### V-02

- Severity: medium
- Category: correctness
- Title: Failed passphrase initialization leaves an IVK-bound vault the next attempt cannot open
- File: `src/yoetz/service/vault.py:464-482` and `:1372-1381`; index adoption in `src/yoetz/adapters/keys/encrypted_vault.py:169-190`
- Evidence: `initialize_passphrase` writes the encrypted store (`_open_store_and_sentinel` → `EncryptedVaultStore.initialize`, which publishes `vault-index.json` authenticated under the new IVK) before `publish_mode` writes the installation marker. The `except` clause handles only `EncryptedVaultError`, `VaultPassphraseError`, and `OSError`. It closes the in-memory store and sets `LOCKED`, and it does not remove the vault directory. `publish()` in the marker store raises `RuntimeError` for a throttle-digest or marker conflict (`daemon.py` `publish`, around `:2501-2514`). That exception is not in the handler, so `self._state` stays `UNLOCKING` and `self._store` stays open with the IVK. On any later attempt, `EncryptedVaultStore.initialize` sees the existing index and verifies its MAC with the new IVK (`:186-187`). The MAC does not match, so the call raises `index_tampered`, which the handler reports as `vault_tampered`.
- Failure scenario: The first passphrase initialization persists `vault/vault-index.json` (and possibly the sentinel record) and then the marker write fails, or the process dies before the marker is durable. The installation is still uninitialized. The next `initialize_passphrase`, including after restart, generates a new IVK, fails the index MAC, and refuses. The passphrase that wrapped the first IVK cannot be used because that IVK was never recorded in a marker, and the new passphrase cannot adopt the orphan index.
- Suggested fix and test: Publish is the commit point. Either write the marker in the same crash journal as the vault directory (rename a staged `vault` into place only after the marker fsync, with a restart reconciler), or on failure delete a vault directory that has no installation marker, using the same `lstat` / `O_NOFOLLOW` rules as record publish. Catch `RuntimeError` from `publish_mode` on this path, close the store, return to `LOCKED`, and do not leave `UNLOCKING`. Test: a `publish_mode` double that raises `RuntimeError` after the store factory has created an index; assert the service is `LOCKED` / uninitialized and that a second `initialize_passphrase` with a new secret succeeds. A second test kills after the index exists and before the marker, restarts against that directory, and asserts either clean retry or an explicit repair reason, not a permanent `vault_tampered`.
- Confidence: high
- Related tests: `tests/unit/service/test_vault_state.py`, `tests/integration/service/test_encrypted_vault.py`, `tests/subprocess/test_service_unlock_boundary.py`. The state tests cover the happy publish path, not a publish failure after the index exists.

### V-03

- Severity: low
- Category: security
- Title: Root-rotation commit treats a symlink stage as a directory
- File: `src/yoetz/service/daemon.py:2687-2731` (called from `VaultService.commit_root_rotation`)
- Evidence: The stage check is `staged_vault.is_dir()` and `vault.is_dir()`. `Path.is_dir()` follows symlinks. The name checks are lexical (`parent`, `name.startswith`, `name.endswith`). `prepare_root_rotation` creates the stage with `ensure_owner_only_dir`, which rejects a symlink at creation, but the commit check does not use `lstat`. On Linux, `os.rename` of a symlink renames the symlink itself. After `os.rename(staged_vault, vault)`, `bundle/vault` can be a symlink. Later record IO uses `O_NOFOLLOW` only on the final path component, so opens still follow a symlink directory.
- Failure scenario: After a rotation stage is created and before commit, something replaces `.vault.root-<generation>.<random>.tmp` with a symlink to another directory the user can write. Commit accepts `is_dir()`, moves the real vault aside, and installs the symlink as `vault`. New root-state and record files are created in the link target. If that target is not owner-only, ciphertext file names leave the private bundle tree. Record mode is still `0600`, so this is not a plaintext disclosure by itself. A symlink to a directory the committer does not control fails the later private-file checks or the rename.
- Suggested fix and test: Require `lstat` to show a real directory owned by the euid, mode `0700`, and not a symlink, for both the stage and the live vault, immediately before each rename. Repeat the check on the destination after rename. Test: replace the stage with a symlink to another directory and assert `installation_root_rotation_stage_invalid` with the live `vault` directory unchanged.
- Confidence: high on the check; medium on practical exposure, because the stage lives under the owner-only bundle and ADR-004 excludes a compromised UID. The missing `lstat` is still inconsistent with `ensure_owner_only_dir` and with recovery's `installation_snapshot_symlink_forbidden` path.
- Related tests: `tests/unit/service/test_vault_state.py`, `tests/integration/service/test_encrypted_vault.py`. No test plants a symlink stage at the commit check.

### V-04

- Severity: low
- Category: security
- Title: Rejected keyring payload is not overwritten on the decode failure path
- File: `src/yoetz/adapters/keys/os_keyring.py:856-863`
- Evidence: `_decode_entry` copies the decoded IVK and correlation into `bytearray`s, then raises `ValueError` when either length is not 32. The `except` maps that to `OSKeyringError("entry_invalid")` and does not call `_overwrite` on those buffers. The success path does wipe them, via `SecretMemoryPort.capture`. ADR-004 disclaims perfect zeroization of copies made inside cryptographic libraries. These two buffers are owned by this function.
- Failure scenario: A keyring entry decodes far enough to produce key material and then fails the length check (truncated or padded payload). The bytearrays remain until garbage collection. This does not reveal the key to another user. It drops the best-effort wipe this adapter applies on the success path.
- Suggested fix and test: Initialize both buffers to `None` and overwrite them in a `finally` before re-raising `entry_invalid`, including when only one decode succeeded. Test with a canonical JSON payload whose `ivk` decodes to a length other than 32 and assert the function raises `entry_invalid`. A test cannot see freed heap; assert the wipe helper is invoked by structuring the buffers so the `finally` is unavoidable, or inspect the buffers if the test holds references (it should not). The useful assertion is that every assigned `bytearray` is zeroed before the exception leaves `_decode_entry`.
- Confidence: high
- Related tests: `tests/integration/objects/test_key_backends.py`, `tests/capability/test_service_keyring_unlock.py`.

## Examined, not filed

Nonce reuse. Vault records use a fresh 32-byte DEK and a fresh 12-byte nonce for one AES-256-GCM encryption; the DEK is wrapped with AES-256-KW (`encrypted_vault.py` `_encrypt_frame`). Root-state encryption draws a new 12-byte nonce per write under the active root. Bundle object encryption is specified the same way in ADR-004. No path reuses a nonce under a retained key.

KDF parameters. Passphrase, portable recovery, and installation recovery all use Argon2id version 19, memory 262144 KiB, time cost 3, parallelism 1, then HKDF-SHA256 into separate wrap and MAC keys (`vault_passphrase.py`). The parser rejects parameters outside memory 64–1024 MiB, time 1–10, and parallelism 1–8, and the MAC covers those parameters, so a disk edit cannot silently weaken them. The 16-byte minimum passphrase length is a policy floor, not a weak KDF.

Passphrase comparison. Envelope authentication uses `hmac.compare_digest` before AES-KW unwrap. Confirmation in the CLI uses `compare_digest`. On this interpreter, unequal lengths return false rather than raising, which is what `test_auto_unlock_rotation_can_stage_an_exact_user_selected_value` depends on. Keyring read-back uses ordinary string equality on a value this process just wrote; a hostile keyring backend already sees `set_password`.

Unlock bypass. `UnlockCoordinator` reserves the throttle record before passphrase KDF, charges failure, and resets only after ready. Installation recovery computes the clean throttle generation in memory and commits it only after `recover_passphrase` succeeds (`unlock.py` `prepare_recovery_record` / `commit_recovery_record`), so starting recovery does not clear an accumulated delay. Challenge objects are compared by identity. Secret purposes are not interchangeable. Recovery still has to open the live sentinel with the unwrapped IVK. Missing recovery metadata is accepted only when `admits_clean_restore` matches a self-contained clean-restore record; the IVK check remains.

Auto-unlock versus the throttle. Startup and soft re-ready call `VaultService.unlock` on at most three keyring slots without `reserve_attempt` (`daemon.py` around `:4060` and `:2108`). That skips `repair_required` and the delay. The values are the platform store's own passphrase, not remote guesses. ADR-008 already says a same-UID process is outside the model. Not filed as an authentication bypass. The unkeyed throttle checksum is the same class of control: it detects corruption, and ADR-008 does not claim it resists the file's owner.

Upgrade versus a running service. `run_before_ready` requires the singleton holder. Supersede sends `SIGTERM` and waits until that holder releases `service.lock` before a successor starts. Bundle migrations for versions 13–15 run inside one APSW transaction (`migrations.py` `run_migrations`), and 12, 13, and 14 are all legal resume points. A version that is neither a source nor 15 fails closed with `migration_unsupported` or `rollback_required` and is not published as READY. The upgrade CLI prints `CODEX_HOME` in the plan; it does not print vault material.

Secret ingress. Passphrases are read from the trusted console or generated inside the process. `cli/unlock.py` and `cli/elevated.py` do not take a passphrase from argv. `load_config({}, os.environ, None)` is used to find the data directory, not to import a secret. Elevated provider credentials are an explicit bytearray argument after a warning acknowledgement, which ADR-008 records.

Symlink discipline elsewhere. Record publish, throttle IO, recovery copy, and backup file copy use `O_NOFOLLOW` plus owner/mode/`nlink` checks. `OfflineInstallationRecoveryLease` flocks the same `service.lock` as the daemon (`LOCK_EX | LOCK_NB`), so a restore cannot proceed while the service holds that inode. Neither opener uses `O_NOFOLLOW`. A symlink planted in the state directory is a same-UID write, which ADR-004 excludes; both sides would follow the same link and still exclude each other.

Library copies. `derive_passphrase_subkeys` and HKDF pass `bytes(secret)` into `cryptography`. ADR-004 states that those copies are outside the adapter and that no perfect zeroization claim is made. Not filed separately from V-04.

## Coverage

No nonce-reuse, weak-Argon2, or argv/env passphrase ingress defect was found in this scope. The durable gaps are the unverified snapshot install (V-01), the orphan vault after a failed first initialization (V-02), the symlink-following rotation commit check (V-03), and the keyring decode wipe miss (V-04).


---

# SQLite storage

Source notes: `08-sqlite.md`.

# SQLite adapter audit

Read-only review of `src/yoetz/adapters/sqlite/` (all 15 modules), source SQL under `migrations/bundle/` and `migrations/catalog/`, and the storage tests that lock those paths. No source edits. No live databases opened.

SQL that interpolates identifiers uses module constants (`_COLUMNS`, `_PROJECT_COLUMNS`, `_ROUTE_FIELDS`) or `?` placeholders sized with `','.join('?' ...)`. No user-controlled string is concatenated into SQL. Catalog `active_session_id` is `UNIQUE`. Bundle event reads that filter `ingestion_seq` use the primary key. Migration DDL is applied inside one APSW transaction and `PRAGMA user_version` moves in that same transaction, so a failed upgrade rolls the schema back. Those hunts did not produce a finding.

## Q-01

- **Severity:** high
- **Category:** corruption recovery
- **Title:** `recover_bundle` rejects every bundle schema this binary writes
- **File:** `src/yoetz/adapters/sqlite/recovery.py:509-515`
- **Evidence:** After the fence check, any `storage_schema_version > 1` clears the bundle fence and returns `MANUAL_INTERVENTION` / `SCHEMA_UNSUPPORTED`. It never reaches `complete_interrupted` or `rebuild_projection`. The supported bundle version is 15 (`src/yoetz/adapters/sqlite/connection.py:55-56`). `initialize_bundle` overwrites the caller's seed and stores that current version (`src/yoetz/adapters/sqlite/migrations.py:297-298`). `tests/integration/storage/test_quarantine_and_recovery.py:262-270` locks version 2 as unsupported. `recover_bundle` is only called from that test; the service path uses `inspect_recovery_state` and `acquire_bundle_ownership` and does not call `recover_bundle`.
- **Failure scenario:** A caller that follows the adapter's public recovery entry on a bundle created or migrated by this revision gets a manual-intervention result and a cleared fence. Interrupted-write completion and projection rebuild in this function are unreachable for every non-v1 ledger. The function does not delete event rows; it refuses the bundle.
- **Suggested fix:** Compare against the supported version, and allow every version the migration runner can bring current.

```python
if state.storage_schema_version > _SUPPORTED_BUNDLE_SCHEMA_VERSION:
    _clear_fence(...)
    return _result(..., reason=RecoveryReason.SCHEMA_UNSUPPORTED)
if state.storage_schema_version < _SUPPORTED_BUNDLE_SCHEMA_VERSION:
    # fail closed until run_migrations has committed, then re-inspect
    ...
```

- **Test:** Extend `test_unsupported_schema_never_opens_writer` so version 15 (and each version `run_migrations` accepts) is not `SCHEMA_UNSUPPORTED`, and version 16 still is. Assert `complete_interrupted` runs for an interrupted v15 tail.
- **Confidence:** high
- **Related tests:** `tests/integration/storage/test_quarantine_and_recovery.py`

## Q-02

- **Severity:** high
- **Category:** missing transaction
- **Title:** Ledger recovery reads across awaits with no snapshot
- **File:** `src/yoetz/adapters/sqlite/repository.py:730-892` and `:1292`
- **Evidence:** `_recover_projection` holds `self._lock` but never issues `BEGIN`. It `fetchall()`s every event (`:740-745`), then awaits per row inside `_decode_durable_record` (`:628-632` object reads, `:749-751` `sleep(0)`), then reads `operations`, `semantic_jobs`, and `semantic_attempts` (`:779`, `:861`, `:1164`). `_requires_recovery` is cleared only at `:1292`, so the mixed image is the process's ledger. `SqliteLedger` is lazy: `_requires_recovery` is true whenever the head sequence is non-zero (`:339`). `ready_composition.open_importer` opens a second connection on the same file via `SqliteWriterThread` (`src/yoetz/service/ready_composition.py:2296-2306`) before the first `load_frontier` / append triggers recovery. That writer uses `BEGIN IMMEDIATE` (`importer.py:1099-1100`).
- **Failure scenario:** An import commit lands after the event join and before the `operations` read. In-memory `records` omit the new events while a complete operation's `first_ingestion_seq`/`last_ingestion_seq` refer to them. Check reconstruction slices `records[first-1:last]` (`:955`) against the short list, then latches that state for the life of the process. The symmetric window (operations read from an older snapshot than events) attaches stale operation rows to a newer chain.
- **Suggested fix:** Take one read snapshot before any await, and do not clear `_requires_recovery` if a writer committed during the read.

```python
self._db.execute("BEGIN")
try:
    rows = self._db.execute(EVENT_JOIN).fetchall()
    writers = self._db.execute(WRITERS).fetchall()
    pending = self._db.execute(PENDING_OPS).fetchall()
    complete = self._db.execute(COMPLETE_OPS).fetchall()
    jobs = self._db.execute(JOBS).fetchall()
    attempts = self._db.execute(ATTEMPTS).fetchall()
    head = self._db.execute(
        "SELECT ingestion_seq, entry_digest FROM events "
        "ORDER BY ingestion_seq DESC LIMIT 1"
    ).fetchone()
finally:
    self._db.execute("ROLLBACK")
# decode, object IO, and replay only after the snapshot is copied
```

Re-read the head after replay and restart recovery if it moved. Keep the importer writer blocked or compare `owner_generation` inside that snapshot.
- **Test:** Two connections on one bundle. Append through the ledger, start `_recover_projection`, and from the importer connection insert one more committed event plus a complete operation during the first `open_verified` await. The recovered `records` and `operations` must name the same frontier, or recovery must retry. No sleep-based wait: the test hook is the object-store await that already exists.
- **Confidence:** high
- **Related tests:** `tests/integration/storage/test_quarantine_and_recovery.py`, `tests/conformance/adapters/test_memory_sqlite_parity.py`, `tests/property/test_ledger_state_machine_sqlite.py`

## Q-03

- **Severity:** medium
- **Category:** N+1 / unbounded SELECT
- **Title:** Recovery issues one locator query per event and one event-id query per completed operation
- **File:** `src/yoetz/adapters/sqlite/repository.py:595-599` and `:884-890`
- **Evidence:** The recovery join already selects `event_projection_locators` columns (`:741-744`) but `_decode_durable_record` queries `redaction_target_object_ids` again by `event_id` (`:595-599`). Each `state='complete'` operation then runs `SELECT event_id FROM events WHERE ingestion_seq BETWEEN ? AND ?`. Both run while the lock is held, and the per-event query sits on the path that awaits. The event statement itself is unbounded: `fetchall()` of every `canonical_entry` (`:740-745`). That full read matches event-sourced replay; the extra per-row queries do not.
- **Failure scenario:** A long task pays O(events + completed operations) round trips on the service thread before the snapshot in Q-02 is even consistent. Each extra query is another window for the importer commit described there.
- **Suggested fix:** Select `l.redaction_target_object_ids` in the join. Load structural ids with one grouped query, or derive them from the already-copied event rows (`ingestion_seq`, `event_id`) in memory.

```sql
SELECT e.canonical_entry, e.projection_status, l.logical_key,
       l.canonical_payload_digest, l.redaction_target_event_ids,
       l.redaction_target_object_ids
FROM events AS e
JOIN event_projection_locators AS l USING (event_id)
ORDER BY e.ingestion_seq
```

- **Test:** A bundle with N events and M completed operations should execute a fixed number of recovery statements (the snapshot batch), not N+M. Assert via APSW's trace hook or a counting connection wrapper.
- **Confidence:** high
- **Related tests:** `tests/integration/storage/test_append_and_replay.py` (recovery is triggered by `sqlite_for` reopen), `tests/property/test_ledger_state_machine_sqlite.py`

## Q-04

- **Severity:** medium
- **Category:** locking
- **Title:** Catalog commit failure leaves the singleton transaction open
- **File:** `src/yoetz/adapters/sqlite/start_catalog.py:165-175`, `lineage_catalog.py:140-150`, `host_lineage.py:127-138`, `project_operations.py:299-307`
- **Evidence:** Each helper runs `COMMIT` only when the body did not raise, and `ROLLBACK` only when it did. `COMMIT` itself is outside that handler. `SqliteLedger` does the opposite: on any failure it rolls back when `get_autocommit()` is false (`repository.py:2053-2056`, `:1922-1926`). The catalog connection is process-lifetime (`open_catalog_writer`). `busy_timeout` is 5000 ms (`connection.py:51`, `:256`). A second catalog connection exists on quarantine (`ready_composition.py` `persist_quarantine` opens another writer) and during maintenance.
- **Failure scenario:** `COMMIT` raises `BusyError` after the body succeeded. The context manager propagates and does not roll back, so the connection stays inside a transaction. The next `BEGIN IMMEDIATE` raises `SQLError` ("cannot start a transaction within a transaction"), which these helpers only map to `BUNDLE_BUSY` for `BusyError` on `BEGIN`. Catalog writes fail until the process restarts.
- **Suggested fix:** Same shape as the ledger:

```python
def __exit__(self, exc_type, exc, tb) -> Literal[False]:
    try:
        if exc_type is None:
            self._db.execute("COMMIT")
        elif self._db.get_autocommit() is False:
            self._db.execute("ROLLBACK")
    except BaseException:
        if self._db.get_autocommit() is False:
            self._db.execute("ROLLBACK")
        raise
    return False
```

- **Test:** Inject `BusyError` from `COMMIT` on a catalog connection, then `BEGIN IMMEDIATE` again. The second begin must succeed and must not see the uncommitted row. Cover all four helpers.
- **Confidence:** high on the control flow; medium that `COMMIT` hits `SQLITE_BUSY` in WAL (it does when another connection holds the write lock past `busy_timeout`)
- **Related tests:** `tests/unit/adapters/test_sqlite_project_coordination.py`, `tests/integration/storage/test_start_catalog_state_machine.py`, `tests/integration/storage/test_lineage_operation_admission.py`

## Q-05

- **Severity:** medium
- **Category:** WAL / durability
- **Title:** Automatic checkpoints are off and nothing in the service runs the manual one
- **File:** `src/yoetz/adapters/sqlite/connection.py:267-273` and `:726-731`; `repository.py:2586-2610`
- **Evidence:** Every writer sets `wal_autocheckpoint=0` and `synchronous=2` (FULL). ADR-003 says checkpoints are "owner-run bounded PASSIVE". `run_passive_checkpoint` is only referenced from `tests/integration/storage/test_checkpoint_and_wal_bounds.py`. `SqliteWriterThread._run` checkpoints on thread exit and swallows every exception (`:726-731`). The long-lived ledger connection is closed with `_close_db` and never calls `run_passive_checkpoint` (`ready_composition.py:2376-2377`). The page estimate treats the WAL as raw pages (`repository.py:2599-2604`); a WAL frame is 24 bytes plus the page, after a 32-byte header, so the threshold is not a frame count. `synchronous=FULL` still makes a successful `COMMIT` durable in the WAL. `docs/OPEN_QUESTIONS.md` E-005 defers soak budgets and says no operational-threshold claim ships; it does not add a caller.
- **Failure scenario:** A route that stays open keeps appending. The WAL grows for the life of the process because autocheckpoint is disabled and the owner never calls PASSIVE. Disk exhaustion then fails later commits. A swallowed checkpoint error on importer-thread exit leaves the same file. Frame accounting, if a caller used the API, checkpoints earlier or later than the requested page budget.
- **Suggested fix:** From the ledger connection, on a bounded interval and on close:

```python
row = self._db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
# busy, log, checkpointed = row
# if busy: leave the WAL; do not treat a swallowed error as success
```

Count frames as `(wal_bytes - 32) / (page_size + 24)` when `wal_bytes >= 32`. Do not swallow checkpoint errors on `SqliteWriterThread` close; log the bounded reason. FULL sync stays as it is.
- **Test:** Existing `test_owner_only_passive_checkpoint` covers the API. Add a test that a ledger left open across many appends, with no call to `run_passive_checkpoint`, grows `-wal` past a small frame budget, and that one explicit PASSIVE call after the last reader closes moves `checkpointed`. Assert the frame formula against `PRAGMA wal_checkpoint` `log`.
- **Confidence:** high
- **Related tests:** `tests/integration/storage/test_checkpoint_and_wal_bounds.py`, `tests/integration/storage/test_build_and_pragma_gate.py`

## Q-06

- **Severity:** medium
- **Category:** path symlink checks
- **Title:** Database open checks the main file, then reopens it without `NOFOLLOW`, and never checks `-wal` / `-shm`
- **File:** `src/yoetz/adapters/sqlite/connection.py:211-252` and `:534-548`
- **Evidence:** `_verify_database_file` uses `lstat`, rejects a symlink, requires `nlink == 1`, and uses `O_NOFOLLOW` only for the create-if-missing `os.open`, which it then closes. `_open_verified_writer` opens again with `SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE` and does not set `SQLITE_OPEN_NOFOLLOW`. Nothing `lstat`s `path + "-wal"` or `path + "-shm"`. Object backup reads in `maintenance.py:582-605` open the directory and the member with `O_NOFOLLOW` and `follow_symlinks=False`. `verify_private_local_bundle` rejects symlink path components for the parent, which does not cover a sidecar planted beside a regular database file.
- **Failure scenario:** After a clean close, `ledger.sqlite3-wal` is replaced with a symlink. The next `open_writer` sees a regular main file and SQLite follows the sidecar, so new frames land on the symlink target. A swap of the main file between `lstat` and the APSW open has the same effect for the database itself, because the reopen does not use `O_NOFOLLOW`.
- **Suggested fix:** Open with `SQLITE_OPEN_NOFOLLOW` where the SQLite build supports it (this pin is 3.53.3). Before that open, `lstat` the main file, `-wal`, and `-shm` when they exist; reject symlinks, non-regular files, and `nlink != 1`. Keep the existing parent-path check.

```python
flags = apsw.SQLITE_OPEN_READWRITE | apsw.SQLITE_OPEN_CREATE
if hasattr(apsw, "SQLITE_OPEN_NOFOLLOW"):
    flags |= apsw.SQLITE_OPEN_NOFOLLOW
for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
    if candidate.exists():
        _verify_database_file(candidate, may_create=False)
```

- **Test:** Place a symlink at `db-wal` and expect `StorageUnsafeError` reason `storage_path_unsafe` before any write. Repeat with the main file replaced by a symlink after a passing `lstat` if a test hook can sequence the race; otherwise the sidecar case is deterministic.
- **Confidence:** high
- **Related tests:** `tests/unit/cli/test_storage_path_remediation.py`, `tests/integration/storage/test_build_and_pragma_gate.py`

## Q-07

- **Severity:** medium
- **Category:** clock / ordering
- **Title:** Observation retention drops events by insert id and dedup keys by receipt time
- **File:** `src/yoetz/adapters/sqlite/observation.py:58-59` and `:2052-2077`
- **Evidence:** `_MAX_EVENTS` is 256 and `_MAX_DEDUP` is 4096. Over the event cap, rows are deleted by `id ASC`. Over the dedup cap, keys are deleted by `ingested_at ASC`, and `ingested_at` is `envelope.receipt_time.wire` (`:482-484`). The index is `(workspace_commitment, receipt_time)` (`migrations/bundle/0009.sql:71-72`), not `(workspace_commitment, id)`. Ingest of a new envelope checks `observation_dedup` first (`:450-458`). There is no tie-break when `ingested_at` collides.
- **Failure scenario:** After 4096 ingested envelopes, the last 256 by `id` are the live history. A live row whose `receipt_time` is older than a trimmed dedup key loses its key and keeps the event. The same envelope is accepted again and inserted, so status and `list_envelopes` see a duplicate. Equal receipt timestamps make which dedup key survives depend on SQLite's unordered scan of that timestamp.
- **Suggested fix:** Trim both tables by the same order, and delete a dedup key only when no remaining event still carries it.

```sql
DELETE FROM observation_dedup
WHERE workspace_commitment = ?
  AND dedup_key NOT IN (
    SELECT dedup_key FROM (
      SELECT d.dedup_key
      FROM observation_dedup AS d
      JOIN observation_events AS e
        ON e.workspace_commitment = d.workspace_commitment
      WHERE d.workspace_commitment = ?
      ORDER BY e.id DESC
      LIMIT ?
    )
  );
```

Practical shape: delete events by `id ASC`, then delete dedup rows whose keys are not referenced by a remaining event, using the digest inputs stored on the event (or store `dedup_key` on `observation_events`). Add `ORDER BY ingested_at, dedup_key` if a time-ordered trim remains.
- **Test:** Insert 4096 envelopes with receipt times reversed relative to insert order. Assert the surviving 256 events still have dedup hits, and a replay of the oldest surviving envelope returns `DUPLICATE`.
- **Confidence:** high on the two orders; medium on how often receipt time runs backwards (the column is caller-supplied `receipt_time`, not the insert sequence)
- **Related tests:** `tests/unit/adapters/test_sqlite_observation.py`, `tests/integration/storage/test_migration_0002_observation.py`, `tests/integration/storage/test_migration_0009_observation.py`

## Q-08

- **Severity:** medium
- **Category:** locking / busy-timeout misuse
- **Title:** Observation and coordination writes use deferred transactions beside an immediate writer
- **File:** `src/yoetz/adapters/sqlite/observation.py:480-488`, `observation_verification.py:92-99`, `observation_advice_semantic.py:172-180`, `project_coordination.py:728-743`
- **Evidence:** These paths use `with self._db:`, which starts a deferred `BEGIN`. Ledger and catalog state machines that follow ADR-003 use `BEGIN IMMEDIATE` (`repository.py:2042`, `start_catalog.py:157`, `importer.py:1100`). The observation store is the ledger connection (`repository.py:343-350`). The importer writer is a second connection on that file. `claim_next` updates expired leases and then updates the claimed row inside the deferred transaction (`observation_verification.py:93-99` and the matching block in `observation_advice_semantic.py`). `busy_timeout` retries `SQLITE_BUSY`; it does not repair `SQLITE_BUSY_SNAPSHOT`, which requires a new transaction. `_ledger_write_boundary` catches `ConstraintError` and `MismatchError` only (`observation.py:93`).
- **Failure scenario:** Ingest or `claim_next` reads under the deferred snapshot. The importer commits. The following `UPDATE`/`INSERT` raises `SQLITE_BUSY_SNAPSHOT`, the context manager rolls back, and the expired-lease reclaim rolls back with it. The worker surfaces a driver error instead of a claimed job, and the stale `running` lease stays until a later attempt that does not collide.
- **Suggested fix:** Open these write paths with `BEGIN IMMEDIATE` before the first read, and map `BusyError` to the existing retryable busy error.

```python
self._db.execute("BEGIN IMMEDIATE")
try:
    ...  # reclaim, select, update
    self._db.execute("COMMIT")
except BaseException:
    if self._db.get_autocommit() is False:
        self._db.execute("ROLLBACK")
    raise
```

- **Test:** Hold a second connection's write transaction across `claim_next` / `ingest`. After it commits, the deferred implementation raises `BusyError` and leaves the lease `running`. The immediate implementation either waits out `busy_timeout` or returns `BUNDLE_BUSY` with the reclaim still atomic.
- **Confidence:** high
- **Related tests:** `tests/unit/adapters/test_sqlite_observation.py`, `tests/integration/storage/test_migration_0003_observation.py`, `tests/unit/adapters/test_sqlite_project_coordination.py`

## Examined and not filed

- **Migration non-idempotence.** `0014` rebuilds `events` by insert-select, drop, and rename inside the migration transaction (`migrations/bundle/0014.sql:101-116`). A CHECK failure rolls the transaction back, including `user_version`. The in-script `PRAGMA foreign_keys` is a no-op while that transaction is open; `migrations.py:229-260` and `:368-398` set and restore the pragma outside it, then `foreign_key_check` runs. Catalog `0004` drops and recreates `task_routes_scoped_attachment` so fresh installs that already ran `0001` do not collide (`migrations/catalog/0004.sql:571-576`).
- **Event deletion on corruption.** No `DELETE FROM events` in the adapter. Quarantine updates route or operation state. Observation trim (Q-07) is the retention delete.
- **Semantic-job UPSERT.** `repository.py:1379-1404` uses `ON CONFLICT DO UPDATE` and keeps `created_at` and the first `terminal_at`. That matches the comment about `INSERT OR REPLACE` deleting rows under deferred foreign keys.
- **`ORDER BY event_id` on the operation locator** (`repository.py:887-888`). `OperationResultLocator` sorts structural ids (`ports/ledger.py:746`), so lexicographic order matches the value object.
- **Capture-backlog join.** `observation_capture_tickets` is `UNIQUE (workspace_commitment, logical_identity)` (`migrations/bundle/0011.sql:44`), so the manifest join in `_capture_backlog_unlocked` does not double-count across two active tickets.


---

# Privacy gateway

Source notes: `09-privacy.md`.

# Privacy audit (read-only)

Scope: `src/yoetz/adapters/privacy/`, `src/yoetz/domain/privacy.py`, `src/yoetz/config/privacy.py`, `src/yoetz/config/privacy_desired.py`, privacy-relevant parts of `src/yoetz/application/privacy_policy.py`, `privacy_control.py`, and `egress.py`, with schema/fixture and privacy tests used only to confirm a code disagreement. No source edits. No network calls.

Checked and not filed, because the code does not do the thing:

- Update-check transport (`src/yoetz/adapters/privacy/update_checks.py`) GETs only `https://pypi.org/pypi/yoetz/json`, `trust_env=False`, `follow_redirects=False`, no query string, no installation id, no Yoetz version. Policy admission is the caller (`is_update_checks_permitted` returns false unless both bits are true). Default-on structural update checks are the documented product seed (`docs/protocol/data-egress-and-privacy.md`), not an accidental allow in this adapter. Residual: a fresh `httpx.AsyncClient` still sends its library User-Agent. That is a client fingerprint, not a Yoetz identifier, and it is not filed.
- Wire vs domain policy decode (`catalog.py` `_policy_from_bytes`) skips the `never_send` const on the domain shape. The deny list is not a stored field; `NEVER_SEND_KINDS` is every `ForbiddenDataKind`. Omitting the const does not shrink enforcement. The wire path still rejects a mismatched const (`_policy_from_wire_mapping`).
- Egress does not span-redact. A scanner hit drops the item or blocks the case. The overlap bug in `observability.privacy._replace_sensitive_spans` is outside this dispatch path.
- `config/privacy.py` bootstrap rejects anything but the all-denied seed. `config/privacy_desired.py` only round-trips canonical JSON and does not apply it.
- External dispatch consumes authorization under the gateway lock and refuses if the registry object changed (`gateway.py` 731–746). That path is the contrast for P-04.

---

## P-01

- **Severity:** high
- **Category:** default-allow / consent classifier inversion
- **Title:** `max_bytes=0` and `max_tokens=0` on an enabled channel remove the ceiling, and lowering a ceiling to 0 is classified as a tightening
- **File:lines:** `src/yoetz/application/egress.py:1173-1186`, `src/yoetz/application/egress.py:1202-1214`, `src/yoetz/application/privacy_policy.py:753-767`, `src/yoetz/domain/privacy.py:662-663`, `src/yoetz/adapters/privacy/gateway.py:812-846`
- **Evidence:** `ChannelPolicy` accepts `max_bytes` / `max_tokens` of 0 on an enabled row (`_nonnegative`, and the all-zero rule applies only when `enabled` is false). Admission treats that zero as “no ceiling”:

```1173:1186:src/yoetz/application/egress.py
        if llm.max_bytes > 0 and minimized.byte_count > llm.max_bytes:
            return await self._complete_semantic_predispatch(...)
        if llm.max_tokens > 0 and minimized.token_count > llm.max_tokens:
            return await self._complete_semantic_predispatch(...)
```

```1203:1214:src/yoetz/application/egress.py
        auth_max_bytes = (
            minimized.byte_count if llm.max_bytes <= 0 else min(llm.max_bytes, minimized.byte_count)
        )
        auth_max_tokens = (
            1
            if candidate.purpose == _CREDENTIAL_PROBE_PURPOSE
            else (
                minimized.token_count
                if llm.max_tokens <= 0
                else min(llm.max_tokens, minimized.token_count)
            )
        )
```

The widen/tighten classifier uses strict greater-than, so a move from a positive cap to 0 is not a widening:

```753:761:src/yoetz/application/privacy_policy.py
        "max_bytes",
        ...
        lambda new, old: new.max_bytes > old.max_bytes,
        "max_tokens",
        ...
        lambda new, old: new.max_tokens > old.max_tokens,
```

`_is_tightening` is “no change has `widens=True`”. `privacy_propose_policy` / `privacy_tighten_policy` therefore commit `4096 → 0` without a human decision. The gateway predispatch recheck (`_predispatch_reason`) never compares `case.byte_count`, `len(body)`, or `authorization.max_bytes` / `max_tokens`. The only remaining cap is the type ceiling `MAX_EGRESS_CASE_BYTES` (512 KiB).

- **Failure scenario:** An enabled `llm_inference` row has `max_bytes=4096`. A control client submits the same row with `max_bytes=0` (and/or `max_tokens=0`). The service classifies it as a tightening and commits it. The next semantic case is admitted at the full prepared size, up to 512 KiB, with no channel ceiling. A human who reads “0” as “send nothing” gets the opposite.
- **Suggested fix and test:** Treat 0 on an enabled channel as “admit nothing”, not “unlimited”. In `_semantic_pipeline`, block when `minimized.byte_count > llm.max_bytes` or `minimized.token_count > llm.max_tokens` with no `> 0` guard; stamp `auth_max_*` with `min(policy, case)` even when the policy value is 0. Reject enabled rows with a 0 ceiling in `ChannelPolicy.__post_init__` if 0 is not a supported unlimited sentinel — and if it is, the classifier must mark a decrease-to-unlimited as `widens=True`. In `_predispatch_reason`, refuse when `case.byte_count > authorization.max_bytes` or `case.token_count > authorization.max_tokens`. Test: `tests/unit/application/test_channel_ceilings_enforced.py` currently covers only positive ceilings (`test_oversize_bytes_blocks_admission`). Add (1) enabled `max_bytes=0` blocks a non-empty case, (2) `privacy_policy_changes` from 4096 to 0 has `widens=True` for `max_bytes`, (3) gateway dispatch of a case larger than `authorization.max_bytes` returns `POLICY_DENIED` before mint.
- **Confidence:** high
- **Related tests:** `tests/unit/application/test_channel_ceilings_enforced.py` (positive ceilings only), `tests/unit/privacy/test_policy_and_contracts.py`, `tests/builders/privacy_widenings.py`

---

## P-02

- **Severity:** medium
- **Category:** consent / policy ceiling not enforced
- **Title:** Proposal lifetime is floored at 60 seconds, so a tighter `authorization_ttl_seconds` is ignored
- **File:lines:** `src/yoetz/application/egress.py:1232-1233`, `src/yoetz/adapters/privacy/catalog.py:2790-2793`, `src/yoetz/application/privacy_policy.py:763-766`
- **Evidence:** The channel field is a real policy dimension (human diff, widening when increased). Admission does not use it when it is below 60:

```1232:1233:src/yoetz/application/egress.py
                    expires_at=now
                    + timedelta(seconds=max(60, llm.authorization_ttl_seconds or 60)),
```

`authorization_ttl_seconds=0` is falsy, so `or 60` also rewrites an explicit 0 into 60 before `max`. `CatalogPrivacyAudit.authorize` then mints `expires_at = now + timedelta(seconds=60)` (`catalog.py:2792`) without reading the channel TTL. A configured 10-second cap becomes a 60-second proposal window. The same classifier that treats a larger TTL as a widening (`privacy_policy.py:766`) will commit a reduction to 10 as a tightening, and runtime will still wait 60.

- **Failure scenario:** Human approves `confirm_every_request` with `authorization_ttl_seconds=10`, or a later tightening sets 10. The prepared case stays dispatchable for 60 seconds. `authorize` issues another 60-second authorization from mint time, so the last 60 seconds of that window are a fresh dispatch grant.
- **Suggested fix and test:** Set the proposal expiry to `now + timedelta(seconds=llm.authorization_ttl_seconds)` and block when that TTL is already expired (`<= 0`). Pass the same TTL into `authorize` and set `EgressAuthorization.expires_at` to `min(proposal.expires_at, now + ttl)`, never a hard-coded 60. Test in `tests/unit/privacy/test_catalog_audit.py` and a coordinator test: policy TTL 10, clock advanced 11 seconds, `authorize` / dispatch returns `AUTHORIZATION_EXPIRED` and does not mint. Assert a widening diff still flags 10 → 60, and a stored TTL of 10 is what `prepare_disclosure_proposal` records.
- **Confidence:** high
- **Related tests:** `tests/unit/privacy/test_catalog_audit.py` (expects `timedelta(seconds=60)`), `tests/unit/privacy/test_policy_and_contracts.py`

---

## P-03

- **Severity:** high
- **Category:** catalog drift vs gateway / redaction evidence
- **Title:** Never-send blocks are receipted as a passing secret scan with a zero match count and a zero scanner digest
- **File:lines:** `src/yoetz/adapters/privacy/gateway.py:896-926`, `src/yoetz/adapters/privacy/gateway.py:1038-1041`, `src/yoetz/application/egress.py:1768-1771`, `src/yoetz/application/egress.py:2070-2078`, `src/yoetz/adapters/privacy/local_enforcer.py:58-59`
- **Evidence:** The enforcer’s real profile digest is `sha256:75d5e5545aec001901b1370f502120114b583662fd473229e545553154f4d605` (`local_enforcer.py:58-59`). On the gateway path that actually sees a scan hit, the pre-consume receipt hard-codes a pass:

```922:923:src/yoetz/adapters/privacy/gateway.py
            ReceiptSecretScan(_SCAN.version, _SCAN.profile_digest, 0, True),
```

`_preconsume_failure` is the `NEVER_SEND_DETECTED` return (`gateway.py:654-657`). The attempted-receipt path also hard-codes `match_count=0, passed=True` (`gateway.py:1040`) and never stores the finding count. The coordinator’s pre-dispatch receipt, including `BLOCKED_FORBIDDEN_DATA`, uses a different placeholder and still says the scan passed:

```1770:1770:src/yoetz/application/egress.py
            ReceiptSecretScan("observability-sensitive-content-v1", f"sha256:{'0' * 64}", 0, True),
```

Local blocks pass the real match count into `ReceiptSecretScan` (`egress.py:2156`, `passed` is `match_count == 0`) but the digest is still 64 zero hex digits (`egress.py:2076-2077`), not `_SCANNER_PROFILE_DIGEST`. Resume builds `MinimizedDisclosure` with `scanner_registry_version="resume"` and `scanner_profile_digest=proposal.policy_digest` (`egress.py:973-986`), and `_semantic_local_receipt` copies that into the receipt (`egress.py:268-273`).

- **Failure scenario:** A case containing `sk-…` or a PEM private-key marker is blocked, which is correct for the bytes. The durable receipt says `secret_scan.passed=true`, `match_count=0`, and either the real digest with a lie about the result (gateway) or an all-zero digest (coordinator). An auditor or a later migration that trusts `passed` will treat a forbidden-data block as a clean scan. Local and network receipts for the same scanner do not share a profile digest, so catalog rows and gateway rows cannot be compared.
- **Suggested fix and test:** One helper should build `ReceiptSecretScan` from `SecretScanRuleset` plus the actual `scan_exact_bytes` result: `match_count=len(findings)`, `passed=(not findings)`. Use it in `_preconsume_failure` when `reason is NEVER_SEND_DETECTED`, in `_complete_semantic_predispatch`, and in `_complete_local_block`. Do not emit the zero digest. Resume must carry the scanner version and digest from the original proposal, not the string `"resume"` or the policy digest. Test: extend `tests/integration/privacy/test_egress_gateway.py` and `tests/unit/privacy/test_receipts.py` so a `BEGIN PRIVATE KEY` / `sk-` payload yields `blocked_forbidden_data`, `passed=false`, `match_count>=1`, and `scanner_profile_digest` equal to `SecretScanRuleset().profile_digest` on both the gateway pre-consume receipt and the coordinator decision receipt.
- **Confidence:** high
- **Related tests:** `tests/unit/privacy/test_receipts.py`, `tests/integration/privacy/test_egress_gateway.py`, `tests/integration/privacy/test_plaintext_canary_sweep.py`, `schemas/privacy/egress-receipt-1.0.0.schema.json` (`secret_scan`)

---

## P-04

- **Severity:** medium
- **Category:** local vs remote / TOCTOU on revocation
- **Title:** Local-model dispatch is not on the gateway authority fence that external dispatch uses
- **File:lines:** `src/yoetz/adapters/privacy/gateway.py:1051-1079`, `src/yoetz/adapters/privacy/gateway.py:731-746`, `src/yoetz/adapters/privacy/gateway.py:1088-1112`, `src/yoetz/adapters/providers/local_model.py:320-321`
- **Evidence:** External admission holds `_lock`, requires the same registry object and authority epoch, and only then calls `audit.consume`. `close_revoked` / `authority_mutation_fence` drops `_registry` and bumps `_authority_epoch` under that same lock (`gateway.py:1104-1109`). Local dispatch reads the registry, scans, then `consume_local` with no gateway lock, then sends:

```1062:1079:src/yoetz/adapters/privacy/gateway.py
        registry = self._current_registry()
        ...
        binding, evaluator = registry.local_model
        ...
        await self._audit.consume_local(...)
        ...
        result = await evaluator.evaluate(case, deadline)
```

`LocalModelEvaluator.evaluate` writes `case.payload` to the socket (`local_model.py:320-321`) with no second registry check. A revocation that lands during `consume_local` still reaches `send`.

- **Failure scenario:** Local-model review has passed the scan and is inside `consume_local`. A policy revocation runs `authority_mutation_fence`, nulls the registry, and closes the previous evaluator only after the lock is released. The in-flight local coroutine still holds the old evaluator and sends the payload after the fence. The external path would have failed the post-await registry identity check and not called `evaluate`.
- **Suggested fix and test:** Mirror the external admission critical section: under `_lock`, re-read the registry, require `registry.local_model` identity and `policy_digest` still match, then `consume_local`, and only then evaluate. If the registry moved, return the unavailable result without sending. Test in `tests/integration/privacy/test_egress_gateway.py`: start `dispatch_local_semantic`, block inside a fake `consume_local`, call `close_revoked`, release consume, and assert the socket `send` was not called.
- **Confidence:** high
- **Related tests:** `tests/integration/privacy/test_egress_gateway.py`, `tests/unit/application/test_semantic_local_egress.py` (calls `_dispatch_approved` directly and does not cover revocation)

---

## P-05

- **Severity:** medium
- **Category:** TOCTOU on consent / authorization expiry
- **Title:** External consume trusts a timestamp taken before render and mint, and the catalog consume does not check `expires_at`
- **File:lines:** `src/yoetz/adapters/privacy/gateway.py:619-632`, `src/yoetz/adapters/privacy/gateway.py:838-841`, `src/yoetz/adapters/privacy/gateway.py:662-746`, `src/yoetz/adapters/privacy/catalog.py:2825-2892`
- **Evidence:** `dispatch_external_semantic` captures `now_utc` once (`gateway.py:621`) and uses it for the expiry check (`gateway.py:840-841`). Rendering, the byte scan, credential mint, and `build_evaluator` all happen after that, and they can await. The later admission lock checks epoch and registry identity, not `authorization.expires_at`. `consume` is called with the original `now_utc` (`gateway.py:742`). `CatalogPrivacyAudit.consume` parses `expires_at` into a local `EgressAuthorization` (`catalog.py:2870`) and never compares it to `now` or to the clock. It writes that stale `now` into `consumed_at` / `dispatch_started_at`.

- **Failure scenario:** Authorization TTL remaining is a few seconds (or the 60-second mint window from P-02). Render or vault mint blocks past `expires_at`. The predispatch check already passed. Consume records the early timestamp and the provider `evaluate` runs. A concurrent reader sees `consumed_at` before the real expiry.
- **Suggested fix and test:** Take a fresh `now_utc` immediately inside the admission lock, refuse when `now_utc >= authorization.expires_at`, and pass that value to `consume`. Inside `consume`, after loading the stored authorization, raise `privacy_authorization_expired` when `parse_rfc3339_millis(expires_at) <= now` before the CAS update. Test: script a clock that jumps during `factory.render`; assert no `evaluate` and no `receipt_pending` row.
- **Confidence:** high
- **Related tests:** `tests/integration/privacy/test_egress_gateway.py`, `tests/unit/privacy/test_catalog_audit.py`

---

## P-06

- **Severity:** high
- **Category:** enforcer bypass / classification default-allow
- **Title:** `sensitive_confidential` is never produced, and the source never-send prefixes are never applied by any producer
- **File:lines:** `src/yoetz/adapters/privacy/local_enforcer.py:60-73`, `src/yoetz/adapters/privacy/local_enforcer.py:221-236`, `src/yoetz/domain/privacy.py:869-873`, `src/yoetz/application/egress.py:1647-1655`, `src/yoetz/application/egress.py:1846-1867`, `src/yoetz/service/ready_composition.py:2618`
- **Evidence:** Classification has three outcomes. Anything that is not a scanner hit and not a structural category becomes ordinary user content. `DataClass.SENSITIVE_CONFIDENTIAL` is not assigned:

```230:236:src/yoetz/adapters/privacy/local_enforcer.py
            data_class = (
                DataClass.SECRET_OR_CRYPTOGRAPHIC
                if source_findings
                else DataClass.PUBLIC_STRUCTURAL
                if item.category in _STRUCTURAL_CATEGORIES
                else DataClass.ORDINARY_USER_CONTENT
            )
```

`PrivacyPolicy` forbids that class on `minimal_external` (`domain/privacy.py:869-873`). `_semantic_decision` only admits items whose `data_class` is in the channel allowlist (`egress.py:1647-1655`). Because the class is never produced, excluding it blocks nothing, and including it admits nothing extra. `CandidateContextItem` has no sensitivity mark the enforcer could read.

The other half of the never-send set is an `origin_ref` prefix table (`credential:`, `environment:`, `keyring:`, `out_of_scope:`, `raw_database:`, `raw_log:`, `raw_stderr:`, `transcript:`, `vault:`). Repo search shows those prefixes only in `local_enforcer.py` and in privacy tests (`tests/unit/privacy/test_local_enforcer.py`, `tests/integration/privacy/test_plaintext_canary_sweep.py`). Production candidates use pointers such as `/case/{section}/{id}` (`semantic_case.py:2764-2776`) and `/credential-probe` (`daemon.py:3624`). A transcript, log, or env blob with a pointer origin and no `sk-` / PEM pattern is `ORDINARY_USER_CONTENT` and is eligible whenever its category is allowed.

`ready_composition.py:2618` constructs `LocalPrivacyEnforcer()` with no provenance resolver. The agent-context category bypass in `egress.py:1850-1858` therefore does not fire in the shipped process (provenance stays `None`). That matches ADR-009’s “forward contract” note. The sensitive-class and prefix gaps do not.

- **Failure scenario:** `minimal_external` allows `ordinary_user_content` and not `sensitive_confidential`. A selected evidence excerpt is personal or proprietary prose without a credential pattern. The enforcer labels it ordinary and the gateway sends it. Separately, a candidate whose bytes are a raw log, but whose `origin_ref` is `/case/targeted_excerpts/…`, is not `UNRESTRICTED_LOG` and is not blocked by the never-send source fence.
- **Suggested fix and test:** Either implement the protocol rule (ambiguous classification takes the stricter class; user-marked or producer-marked sensitive material becomes `SENSITIVE_CONFIDENTIAL` and is denied when the ceiling omits it) or stop advertising the class as an enforced ceiling until a producer and the enforcer agree. For source kinds, have the case builder set the closed prefix (or a typed source enum the enforcer matches), and fail closed when a category such as `transcript_excerpt` is paired with a complete-transcript source. Do not keep a prefix table that no producer writes. Tests: a `minimal_external` policy with `ordinary_user_content` only must block an item explicitly marked sensitive; an item with `origin_ref` `raw_log:…` must be `BLOCKED_FORBIDDEN_DATA` (already true in `test_local_enforcer.py`) and a semantic-case transcript item must take that path too, not only the unit-test prefix.
- **Confidence:** high on the dead class and the unwired prefixes; medium on how much content the case builder will actually place in those categories (the enforcer will not stop it)
- **Related tests:** `tests/unit/privacy/test_local_enforcer.py` (prefix cases and a hand-built `SENSITIVE_CONFIDENTIAL` item at line 348, which the enforcer itself never creates), `tests/conformance/privacy/test_privacy_profiles.py`, `docs/protocol/data-egress-and-privacy.md` lines 19–34 and 151

---

## P-07

- **Severity:** medium
- **Category:** data-use guard vs stated eligibility
- **Title:** The runtime data-use guard does not consider provider human access
- **File:lines:** `src/yoetz/domain/privacy.py:414-427`, `src/yoetz/adapters/privacy/gateway.py:842-845`, `src/yoetz/application/egress.py:1600-1606`
- **Evidence:** `ProviderDataUseProfile.recommendation_eligible` requires a current review window, `customer_content_training == "prohibited"`, and retention `none` or `bounded` with `retention_days_ceiling <= 30`. It does not read `provider_human_access`. Both the coordinator (`_data_use_evidence_current`) and the gateway (`_predispatch_reason`) use that predicate when `require_current_provider_data_use_evidence` is set. The protocol’s eligibility sentence for that evidence includes human access `prohibited|restricted` (`docs/protocol/data-egress-and-privacy.md` lines 100–106). A profile with `provider_human_access="permitted"` or `"unknown"` still passes the guard.

- **Failure scenario:** The standing recipe is committed with the runtime guard on. The bound endpoint’s data-use row says training prohibited, retention none, human access permitted. Admission and dispatch both succeed.
- **Suggested fix and test:** Add `provider_human_access in {"prohibited", "restricted"}` to the predicate used by the guard (separate it from a recommendation helper if the CLI recommendation must stay identical). Test: a profile that differs only in `provider_human_access="permitted"` makes `_predispatch_reason` return `POLICY_DENIED` and the coordinator predispatch block before `prepare_disclosure_proposal`.
- **Confidence:** high that the predicate ignores the field; medium that the protocol sentence was meant to bind the runtime guard and not only the CLI recommendation (the guard calls the same function)
- **Related tests:** `tests/unit/application/test_data_use_evidence_runtime_guard.py`, `tests/unit/privacy/test_fallback_provider_binding.py`

---

## P-08

- **Severity:** medium
- **Category:** catalog scan performance
- **Title:** Every effective-policy and repository-authority read decodes every current policy row
- **File:lines:** `src/yoetz/adapters/privacy/catalog.py:718-749`, `src/yoetz/adapters/privacy/catalog.py:758-786`, `src/yoetz/adapters/privacy/catalog.py:2460-2462`, `src/yoetz/application/egress.py:766-768`, `src/yoetz/application/egress.py:817-820`, `src/yoetz/application/egress.py:1132-1142`
- **Evidence:** `effective_policy` and `repository_authority` both run:

```718:727:src/yoetz/adapters/privacy/catalog.py
        rows = self._db.execute(
            "SELECT policy_canonical, policy_generation FROM privacy_policy_versions WHERE state = 'current'"
        ).fetchall()
        ...
            policy = _policy_from_bytes(cast(bytes, canonical))
            if policy.effective_scope.contains(scope):
```

There is no `installation_id` predicate, even though `carry_forward_repository_authority` already filters machine rows that way (`catalog.py:963-968`). Each row is a full JSON decode plus `PrivacyPolicy` validation. One external admission calls `repository_authority` from `_activate_repository_admitted`, again from `_repository_authority_is_current` after minimization, and again from the second activate (`egress.py:1132-1142`), and the gateway validator calls it again around render and admission (`gateway.py:623`, `664`, `728`). `list_pending_disclosures` does `SELECT COUNT(*) + 1 FROM privacy_audit_records` (`catalog.py:2460`) on every pending list, including terminal rows, only to mint a snapshot generation. `list_receipts` does the same (`catalog.py:2514-2516`).

- **Failure scenario:** Task- and request-scoped current rows accumulate. Each semantic attempt parses all of them several times on the admission path before any provider I/O. Pending-disclosure listing counts the whole audit table. This is latency and lock-hold growth, not a byte leak. `repository_authority` can also `INSERT OR IGNORE` into `privacy_installation_authority` on that read path (`catalog.py:840-857`) outside `_lock`.
- **Suggested fix and test:** Select current rows for the installation, and only the ancestor scope digests that can `contain` the requested scope (machine, and the workspace/task/request digests on the chain). Keep the meet. Cache the decoded current set on `_lock` generation and invalidate in `_insert_policy` / `_supersede`. Replace `COUNT(*)` snapshot generation with `MAX(policy_generation)` or an audit counter maintained on insert. Test: seed N current rows for another installation plus one matching chain; assert `effective_policy` issues a query that does not decode the foreign rows (statement trace or a counter in a test double around `_policy_from_bytes`).
- **Confidence:** high on the full-table decode; medium on how large N is in a real install (task/request rows are allowed by the schema)
- **Related tests:** `tests/unit/privacy/test_effective_policy_intersection.py`, `tests/unit/privacy/test_catalog_audit.py`

---

## Coverage notes

| Hunt | Result |
|---|---|
| Enforcer bypass | P-01, P-06 |
| Default-allow | P-01, P-06, P-07 |
| Catalog drift vs gateway | P-03 |
| Update checks leaking identifiers | Not found in `update_checks.py` |
| Local vs remote | P-04; external revocation fence is the one that holds |
| Redaction failures | P-03 (receipt claims a clean scan). No span redaction on the egress path |
| Policy parse fails open | Not found. Domain decode skips the wire `never_send` const; the const is not what enforces the set |
| TOCTOU on consent | P-02, P-04, P-05 |
| Catalog scan performance | P-08 |


---

# Provider adapters

Source notes: `10-providers.md`.

# Provider adapter audit

Scope: `src/yoetz/adapters/providers/` (`openai_responses.py`, `openai_chat_completions.py`, `openai_responses_factory.py`, `codex_app_server.py`, `local_model.py`, `factory.py`, `data_use_catalog.py`, `fake.py`) and `tests/unit/adapters/providers/`, `tests/integration/providers/`.

Read-only. No source edits. No network calls. Line numbers refer to the tree as read.

## Executive summary

Eight findings. The two that change behavior on an ordinary path are: Chat Completions dispatches a different JSON byte string than the audited canonical body, so the one-attempt transport rejects the call before the credential is injected; and the local-model evaluator never applies its deadline or `timeout_seconds` to the socket read, so a silent peer hangs the attempt. Codex login accepts any HTTPS URL and the CLI may open it. A single factory slot can be overwritten across an `await` between render and `build_evaluator`. Stopping the Codex stderr drain at 64 KiB can stall the child until the deadline. An OS `PermissionError` during Codex launch is reported as an authentication failure. Early evaluator returns do not close the HTTP transport. HTTP 404 / `unsupported_profile` is still a retriable transport outcome.

Destination pinning, TLS verification, single-use credential injection, the 1 MiB response cap, `max_retries=0`, and Codex tool-item rejection held up. No finding of model text being executed as a tool, path, or shell command.

## Findings

### R-01

- Severity: high
- Category: correctness
- Title: Chat Completions evaluate sends non-canonical JSON and the transport rejects it before any provider call
- File: `src/yoetz/adapters/providers/openai_chat_completions.py:193-225`, `src/yoetz/adapters/providers/openai_chat_completions.py:542-567`, `src/yoetz/adapters/providers/openai_responses.py:1098-1104`, `src/yoetz/adapters/providers/openai_responses.py:1204-1212`, `src/yoetz/protocol/canonical.py:293-303`

Evidence: `render_case` stores `canonical_encode(_build_body_object(...))`. `canonical_encode` sorts every object key by UTF-16-BE. `ChatCompletionsEvaluator.evaluate` does not parse that rendering. It splats the insertion-ordered dict from `_build_body_object` (`model`, `messages`, `max_tokens`, then optional `response_format`) into `client.chat.completions.create`. Nested message objects are `role` then `content`. The transport, shared with Responses, requires `body == self._body` where `self._body` is the canonical rendering from `ChatCompletionsExternalFactory.render`.

The Responses evaluator documents why that fails and does the opposite: it `strict_json_parse`s `render_case(case).body` and splats that mapping so key order matches the audited bytes (`openai_responses.py:1204-1212`). `tests/unit/adapters/providers/test_openai_responses_request_shape.py` (`test_pinned_sdk_preserves_the_rendered_responses_body`) locks that the pinned SDK writes the mapping it was given. Chat Completions has no equivalent test; `test_chat_completions_request_shape.py` never constructs `ChatCompletionsEvaluator` or the transport.

A local comparison of the same insertion-ordered object against `canonical_encode` produced different bytes (canonical starts `{"max_tokens":...`; insertion order starts `{"model":...`). Mismatch raises `ValueError("openai_transport_body_mismatch")` in `handle_async_request` before `authorize_attempt`. `classify_provider_failure` has no status code for that `ValueError`, so the result is `SemanticResultUnavailable` / `SemanticFailureClass.TRANSPORT`.

Failure scenario: Any live Anthropic, Gemini, OpenRouter, or xAI chat-completions review renders an audited body, then `evaluate` asks the SDK to serialize a different byte string. The transport refuses, the vault credential is not placed on the request, and the attempt is reported as a generic transport failure. The application retry set treats `TRANSPORT_UNAVAILABLE` as retriable (`src/yoetz/application/semantic_attempts.py:71-77`), so the same mismatch can be repeated up to the physical attempt budget, minting a credential each time and still never calling the provider.

Suggested fix and test: Make `ChatCompletionsEvaluator.evaluate` use the same committed rendering as Responses: `strict_json_parse` the canonical body (or the transport's already-bound bytes) and pass that mapping through unchanged. Do not rebuild `_build_body_object` at dispatch. Add an async test mirroring `test_pinned_sdk_preserves_the_rendered_responses_body` and `test_evaluator_dispatches_the_audited_rendered_body_verbatim` for `client.chat.completions.create`, asserting `capture.request_body == render_case(case, profile).body` for both `provider_enforced` and `prompt_only` profiles, including a credential-probe body. Assert a deliberate key-order drift still fails before `authorize_attempt`.

- Confidence: high
- Related tests: `tests/unit/adapters/providers/test_chat_completions_request_shape.py` (render and `normalize_response` only); `tests/unit/adapters/providers/test_openai_responses_request_shape.py:305-353`; `tests/unit/adapters/providers/test_factory_dispatch.py`; `tests/unit/adapters/providers/test_factory_credential_handle.py`

### R-02

- Severity: medium
- Category: correctness
- Title: Each external factory keeps one mutable render slot, overwritten across the mint await
- File: `src/yoetz/adapters/providers/factory.py:133-156`, `src/yoetz/adapters/providers/openai_responses_factory.py:130-153`, `src/yoetz/adapters/providers/codex_app_server.py:2196-2224`, `src/yoetz/adapters/privacy/gateway.py:643-720`

Evidence: `ChatCompletionsExternalFactory`, `OpenAIResponsesExternalFactory`, and `CodexAppServerExternalFactory` each store the last render in one instance attribute (`_last_rendered` or `_last_case_digest`). `build_evaluator` trusts that slot and raises `*_factory_render_required` / `codex_runtime_factory_render_required` when the binding digest differs. The gateway calls `factory.render(case)`, then `await self._credential_minter.mint(binding)`, then `factory.build_evaluator`. The factories are the long-lived objects in the provider registry, not one object per attempt. There is no lock around the slot.

Failure scenario: Two in-flight reviews for the same binding interleave at the mint await. The second `render` replaces the slot. The first `build_evaluator` sees the other digest and raises. The gateway catches that and returns `PROVIDER_UNAVAILABLE` after a credential handle was already minted (`gateway.py:697-720`). The digest check does stop the first attempt from sending the second case's body. The lost attempt is a false provider outage, and a retry repeats the same window.

Suggested fix and test: Return the rendered body (or digest) from `render` and pass that object into `build_evaluator`, or key the slot by `dispatch_id` and delete it in a `finally`. Do not keep a single last-write field on a shared factory. Test with two concurrent `dispatch_external_semantic` calls on one factory (or a direct render/build interleaving around a mint future) and assert each evaluator's transport is bound to its own body digest, and that a mismatched pair still fails closed.

- Confidence: high
- Related tests: `tests/unit/adapters/providers/test_factory_credential_handle.py` (single-threaded handle type only); no concurrent render test

### R-03

- Severity: high
- Category: correctness
- Title: Local-model evaluator checks the deadline once, then waits on the socket with no timeout
- File: `src/yoetz/adapters/providers/local_model.py:78-100`, `src/yoetz/adapters/providers/local_model.py:297-338`

Evidence: `LocalModelEndpointProfile.timeout_seconds` is required to be an int from 1 to 300 and is never read again. `LocalModelEvaluator.evaluate` returns `SemanticResultTimeout` only when `deadline.expired` is already true before I/O. `await self._handle.send(case.payload)` and `await self._handle.receive(_MAX_LOCAL_RESPONSE_BYTES)` are not wrapped in `asyncio.wait_for` or any other deadline. A response that arrives after the deadline is still `SemanticResultSuccess`; there is no late branch. `LocalModelSocketHandle.close` exists and is not called on the success, failure, or timeout path.

Failure scenario: The peer accepts the payload and does not answer. The check stays inside `receive` past the caller deadline and past `timeout_seconds`. Cancellation still works (`except Exception` does not swallow `CancelledError` on 3.14), but a quiet socket does not cancel itself. The attempt never becomes a bounded timeout, and the handle stays open.

Suggested fix and test: Compute `remaining = deadline.remaining_seconds(now)` and also cap it with `self._profile.timeout_seconds`. `await asyncio.wait_for(...)` around send and receive. On `TimeoutError`, return `SemanticResultTimeout` with `SemanticFailureClass.TIMEOUT`. If receive returns after the deadline, return `SemanticResultLate` or timeout, not success. Call `close()` in a `finally` if this evaluator owns the handle; if the caller owns it, document that and still enforce the wait. Add tests with a handle whose `receive` waits on an `anyio.Event`: one that is set only after the deadline, one that never is set. Assert the never-set case returns timeout without a sleep-as-success, and that `close` ran. There are currently no `LocalModelEvaluator` tests under `tests/`.

- Confidence: high
- Related tests: none

### R-04

- Severity: medium
- Category: security
- Title: Codex login challenge accepts any HTTPS URL and the CLI can open it
- File: `src/yoetz/adapters/providers/codex_app_server.py:1102-1121`, `src/yoetz/cli/codex_subscription.py:565-573`

Evidence: `_login_challenge` requires `type(url) is str`, length 1..8192, `urlsplit` scheme `https`, a non-empty hostname, and no userinfo. It does not restrict the host, port, or path. The returned `CodexLoginChallenge.url` is the original string. `present` in the subscription CLI prints that URL and, when browser login is requested, calls `webbrowser.open(challenge.url)`.

Failure scenario: The app-server login result carries `authUrl` or `verificationUrl` of `https://127.0.0.1/...` or any other HTTPS origin. Yoetz presents it as the Codex sign-in URL and may hand it to the browser. The executable digest check constrains the binary, not the URL inside the JSONL login result. Tests cover a mismatched login mode and a sample `https://chatgpt.com/auth` / `https://auth.openai.com/...` URL. They do not reject a non-OpenAI host.

Suggested fix and test: Allow only the reviewed ChatGPT/OpenAI auth hosts (exact hostname set, HTTPS, no userinfo, bounded path). Reject IP literals and single-label names. Compare the parsed origin, not a substring of the raw URL. Test `_login_challenge` against `https://127.0.0.1/`, `https://169.254.169.254/`, `https://evil.example/chatgpt.com`, and a userinfo URL (already rejected), and assert only the allowlisted hosts produce a challenge. Assert `present` is not what enforces the host rule.

- Confidence: high that the allowlist is absent; medium that a production login result can carry a non-OpenAI URL, because that URL is produced by the digest-pinned app-server from its upstream login response
- Related tests: `tests/unit/adapters/providers/test_codex_app_server.py:516`, `tests/unit/adapters/providers/test_codex_app_server.py:989-1006`

### R-05

- Severity: medium
- Category: performance
- Title: Codex stderr drain stops at 64 KiB, so a full pipe stalls the stdout read until the deadline
- File: `src/yoetz/adapters/providers/codex_app_server.py:314`, `src/yoetz/adapters/providers/codex_app_server.py:717-723`, `src/yoetz/adapters/providers/codex_app_server.py:763-773`

Evidence: `_drain_stderr` counts bytes and `return True` once `count > _MAX_STDERR_BYTES` (65536). It does not keep reading, and it does not close the pipe. `_CodexProcess.read` checks `stderr_task.done()` only before and after `stdout.readline()`. While `readline` is waiting, a child blocked on a full stderr pipe produces no further stdout, so the stderr result is not observed. The read ends only when its timeout fires. Login timeouts are 600s (browser) and 900s (device code); an evaluation read waits for the remaining deadline.

Failure scenario: The child writes more than 64 KiB to stderr and keeps writing. The drain task exits. The stderr pipe buffer fills. The child blocks in `write`. The parent is blocked in `readline` and classifies the attempt as a timeout (`SemanticFailureClass.TIMEOUT` before turn ack, or `post_ack_unknown` after) rather than `codex_app_server_stderr_limit`. Cleanup still runs in `finally`, so the process is not leaked, but the attempt occupies the deadline.

Suggested fix and test: After the cap, keep discarding stderr bytes until EOF so the pipe cannot fill, and set a flag the reader can observe. Alternatively close the stderr transport after the cap so the child gets `EPIPE` instead of blocking. Add an integration-style test with a stub process that writes more than `_MAX_STDERR_BYTES + pipe capacity` to stderr and then nothing to stdout. Assert the attempt fails as `codex_app_server_stderr_limit` well inside a short deadline, and that the child is reaped.

- Confidence: high
- Related tests: `tests/unit/adapters/providers/test_codex_app_server.py` constructs fake `stderr_task` results; it does not fill a real stderr pipe

### R-06

- Severity: low
- Category: quality
- Title: The one-attempt HTTP transport is not closed on early returns, and Responses can leak the client if construction throws
- File: `src/yoetz/adapters/providers/openai_responses.py:1080-1081`, `src/yoetz/adapters/providers/openai_responses.py:1193-1244`, `src/yoetz/adapters/providers/openai_chat_completions.py:529-576`

Evidence: `OneAttemptCredentialTransport.__init__` always constructs `httpx.AsyncHTTPTransport`. `aclose` closes that inner transport, but only if something calls it. Both evaluators return `SemanticResultTimeout` or an import-missing `SemanticResultUnavailable` before `httpx.AsyncClient` is created, so neither `client.close()` nor `http_client.aclose()` runs. `httpx.AsyncClient.aclose` is what closes a custom transport. Chat Completions at least constructs `AsyncOpenAI` inside the `try` that has a `finally` for the HTTP client. Responses constructs `AsyncOpenAI` before the `try` (`openai_responses.py:1227-1235`). If that constructor raises, `http_client` is never closed and the exception leaves `evaluate`, unlike `create()` failures which are classified and not re-raised.

Failure scenario: A review whose deadline is already expired, or a deployment without the `semantic-openai` extra, builds a transport per attempt and drops it. No request is sent, so no socket is necessarily opened, but the pool object is abandoned. A constructor failure on the Responses path also skips `aclose` and surfaces a raw SDK exception to the gateway.

Suggested fix and test: Create the HTTP client only after the deadline and import checks, or call `await self._transport.aclose()` in a `finally` that covers every return, with a guard so a later success path does not double-close incorrectly (httpx close is idempotent; relying on that is fine if tested). Move Responses client construction into the same `try`/`finally` as Chat Completions. Test: an evaluator with an already-expired deadline and a transport whose `aclose` sets a flag; assert the flag is set and the result is timeout. Test: `importlib.import_module` patched to raise `ImportError`; assert `aclose` ran. Test: `AsyncOpenAI` patched to raise; assert the HTTP client is closed and the result is `SemanticResultUnavailable`, not a raw exception.

- Confidence: high
- Related tests: `tests/unit/adapters/providers/test_openai_responses_request_shape.py` closes clients on the success path it drives; no early-return close test

### R-07

- Severity: medium
- Category: correctness
- Title: A filesystem PermissionError during Codex launch is classified as an authentication failure
- File: `src/yoetz/adapters/providers/codex_app_server.py:643-647`, `src/yoetz/adapters/providers/codex_app_server.py:1788-1789`, `src/yoetz/adapters/providers/codex_app_server.py:1811-1812`

Evidence: `_classify_runtime_exception` returns `("unavailable", SemanticFailureClass.AUTHENTICATION)` for every `PermissionError` before it considers `OSError`. `_failure_stage` maps `PermissionError` through `_FAILURE_STAGE_BY_TOKEN.get(str(error), "login_required")`. The only intended `PermissionError` messages are the tokens raised by `_account` (`codex_login_required`, `codex_chatgpt_login_required`). `verify_local_binding` calls `self.executable_path.stat()` and reads `config.toml` inside `_launch`, which runs before `runtime` is assigned. `pathlib` raises `PermissionError` (an `OSError`) when the mode check cannot read the path. `str` of that OS error is not one of the map keys, so the stage becomes `login_required`.

Failure scenario: The pinned executable or `CODEX_HOME` is not readable by the service user. The attempt fails as authentication / login required, and attention handling treats `SemanticFailureClass.AUTHENTICATION` as a sign-in or credential problem (`src/yoetz/service/semantic_attention.py:70-72`). The operator is pointed at ChatGPT login rather than the filesystem mode. The OS path does not enter the stage token, because the message is not copied through; the class is simply wrong.

Suggested fix and test: Catch the adapter's own login failures as a private exception type, or match `str(error)` only when it is exactly `codex_login_required` / `codex_chatgpt_login_required`. Classify any other `PermissionError` / `OSError` before launch as `launch_failed` / `SemanticFailureClass.TRANSPORT` or `UNSUPPORTED_PROFILE`, using the existing `"launch_failed"` branch. Test: a profile whose `stat` raises `PermissionError(13, "Permission denied", "/not/readable")` and assert `failure_class` is not `AUTHENTICATION` and `failure_stage` is `launch_failed`. Keep the existing tests that a missing ChatGPT account is authentication.

- Confidence: high
- Related tests: `tests/unit/adapters/providers/test_codex_app_server.py` login and account tests; no `stat` `PermissionError` test

### R-08

- Severity: low
- Category: performance
- Title: HTTP 404 and unsupported_profile are still retried as transport failures
- File: `src/yoetz/adapters/providers/openai_chat_completions.py:475-478`, `src/yoetz/adapters/providers/openai_responses.py:976-984`, `src/yoetz/service/ready_composition.py:2951-2977`, `src/yoetz/application/semantic_attempts.py:71-77`, `src/yoetz/application/semantic_attempts.py:379-384`

Evidence: Chat Completions maps status 404 to `SemanticFailureClass.UNSUPPORTED_PROFILE` and comments that retrying the same binding cannot help. Responses does not special-case 404; it falls through to `SemanticFailureClass.TRANSPORT`. `ready_composition._map_provider_outcome` turns every unavailable result other than rate limit, quota, and a post-ack transport `OUTCOME_UNKNOWN` into `SemanticReason.TRANSPORT_UNAVAILABLE`, including `unsupported_profile`, `provider_outage`, and authentication. `is_retriable_semantic_outcome` refuses another attempt only for `AUTHENTICATION` and `AUTHORIZATION`. `UNSUPPORTED_PROFILE` is not in that set. `TRANSPORT_UNAVAILABLE` is retriable, up to `physical_attempt_budget` (1 + min(configured retries, 2)).

Failure scenario: A chat-completions host returns 404, or a Responses host returns 404. The service classifies the public reason as transport and can send the same case up to three times. Each time the gateway mints a credential and performs one HTTPS POST. A 404 is not repaired by a retry. Provider-outage 5xx retries are consistent with the documented transient set; 404 and unsupported profile are not.

Suggested fix and test: Map 404 to `UNSUPPORTED_PROFILE` in `classify_provider_failure` for Responses as well as Chat Completions. In `is_retriable_semantic_outcome`, return false for `UNSUPPORTED_PROFILE` (and keep authentication/authorization non-retriable). Do not collapse that class into the retriable transport reason, or if the public reason must stay `transport_unavailable`, still consult `failure_class` the way authentication already is. Test a 404 `APIStatusError`-shaped object through both classifiers, then through `should_retry_after`, and assert no second physical attempt. Existing chat tests lock the class on the classifier only (`test_chat_completions_request_shape.py:309-320`); they do not lock the retry decision.

- Confidence: high
- Related tests: `tests/unit/adapters/providers/test_chat_completions_request_shape.py:295-320`; no Responses 404 test

## Reviewed and not filed

- Owner-declared origins are HTTPS host and optional port only (`parse_https_origin` in `src/yoetz/config/models.py:238-267`, applied by `openai_responses_factory.py:103-118`). The hostname pattern also matches IP literals such as `127.0.0.1`. ADR-014 defines that as an owner-supplied origin, not a free `base_url`. The transport forces `https`, checks host, port, and an allowlisted path, sets `trust_env=False`, `verify=True`, `retries=0`, and `max_retries=0`. A second request on the same transport raises `openai_transport_already_consumed`, so a redirect is not followed with the bearer token. Not filed as SSRF.
- Response bodies are capped at 1,048,576 bytes, including chunked bodies with a missing or small `Content-Length`. Non-identity `Content-Encoding` is refused. Covered by `test_openai_responses_request_shape.py`.
- Credentials are injected only in `inject_and_start`. Failure results use closed tokens. `classify_provider_failure` does not interpolate exception text. The real key is not the SDK `api_key` (`yoetz-fixed-nonsecret-sentinel`). No log statement in these modules prints the header. A caught `httpx` exception can still hold the request, including `Authorization`, until the handler returns; that object is not re-raised on the `create()` path.
- Model output is parsed with `strict_json_parse` and `normalize_judgment`. Cited refs and enums are schema-checked. Chat and Responses do not read `tool_calls`. Codex rejects server-initiated requests (`codex_app_server_tool_request_forbidden`) and item types outside `agentMessage`, `contextCompaction`, `plan`, `reasoning`, and `userMessage`. Judgment text becomes `SemanticJudgment` fields. It is not used as a method name, path, or argv.
- A successful Codex attempt may carry `failure_stage="token_usage_invalid"` when a later usage snapshot regresses. That is locked by `test_cumulative_token_usage_regression_keeps_larger_snapshot_and_marks_gap` and does not change the success verdict.
- `fake.py` is network-free and not registered by `factory.py`. `data_use_catalog.py` does not lend one provider's posture to another; unknown and owner-declared records are not recommendation-eligible (`training="unknown"`). Catalog dates (`expires_at` 2026-11-04, Codex 2026-11-30) are still in the future relative to 2026-09-24.
- Codex `profile.timeout_seconds` is not read inside the evaluator. Production builds the `Deadline` from that config field (`ready_composition.py` around the `timeout_seconds` argument). Per-read waits use the remaining deadline. Not filed separately from R-03.


---

# Host integrations

Source notes: `11-host-integrations.md`.

# Host integration audit (read-only)

Scope: Cursor, Claude Code, and Codex host adapters named in the brief, plus the CLI wrappers that call them where those wrappers change the write or spawn behavior. Docs were opened only to check whether a code defect contradicts a recorded decision (`docs/runbooks/codex-integration.md` on absolute Codex registration, ADR-023 on the Claude bare-`yoetz` dogfood failure). No services were started and no source was modified.

Reviewed and not filed, because the code fails closed or the sibling test locks the behavior:

- `toml_tables.py` removes a generated block only when the byte span, after trailing-newline normalization, equals that block. A second header, a CRLF mismatch, or an owner edit makes `exact_table_span` return `None`, so strip is a no-op rather than a partial delete. Codex grant/revoke re-parse with `tomllib` before trusting an append (`host_admission._codex_after`, `codex_marketplace._activated_config_bytes`).
- Host-admission JSON edits re-serialize the whole object, but `strict_json_parse` rejects non-objects, duplicate keys, and non-integers, and list membership other than the edited key is preserved. Cursor IDE case-folding is intentional (`test_cursor_partial_state_names_the_missing_surface_and_grant_completes_it`).
- Codex external MCP registration (`codex_mcp.py`) argv-passes `mcp add` / `mcp remove`, refuses a foreign entry, and binds an absolute console script when `installed_launcher()` can prove one. Removal exists.
- Cursor project MCP walks path components with `O_NOFOLLOW`, rejects `..`, and replaces `mcp.json` through a pinned directory descriptor.
- Claude and Cursor native plugin renders quote the launcher with `shlex.quote` and reject non-canonical target ancestors. Hook event names in those renders are constants.
- `linux_artifact_presence.py` moves the account password only through the console and an anonymous pipe into a fixed `sys.executable -I -c` worker. It is not written into host config. `macos_artifact_presence.py` passes the prompt as an `osascript` argument, not as script source.
- Codex binary discovery caps successful version probes at 16 and points `CODEX_HOME` at a private temp directory for `--version`.

## H-01

- Severity: high
- Category: registration that can supersede a live install
- Title: Codex project plugin hooks and plugin MCP still launch bare `yoetz`
- File: `src/yoetz/adapters/integrations/codex_plugin.py:154-156`, `src/yoetz/adapters/integrations/codex_plugin.py:307-314`, `src/yoetz/adapters/integrations/codex_plugin.py:319-348`; copied into the host cache by `src/yoetz/adapters/integrations/codex_marketplace.py:722`, `src/yoetz/adapters/integrations/codex_marketplace.py:1045-1048`, `src/yoetz/adapters/integrations/codex_marketplace.py:1938`; setup writes that tree from `src/yoetz/cli/setup.py:1457`
- Evidence: `_hooks_json` hard-codes `yoetz hooks observe`, `yoetz hooks spool`, `yoetz hooks session-start`, and `yoetz hooks user-prompt-submit`. `_mcp_json` sets `command` to `MCP_SERVE_COMMAND[0]`, which is the string `yoetz` (`src/yoetz/ports/harness_mcp.py:35`). `render_plugin_install_tree` is what marketplace activation compares and writes into the Codex plugin cache. Claude and Cursor native renders instead call `resolve_yoetz_launcher` and embed the absolute executable (`claude_code_integration.py:725`, `cursor_integration.py:748`). External Codex registration in `codex_mcp._desired_command` uses `installed_launcher()` and refuses to fall back to `PATH` when a console script exists but cannot be proved. The runbook states the failure mode of a bare command: a test runtime earlier on `PATH` reaches the everyday endpoint and its restart advice supersedes the live service (issue #604). ADR-023 records the same split-brain for Claude hooks and `.mcp.json`, which those carriers then fixed. The Codex project-plugin carrier was not.
- Failure scenario: Setup or marketplace activation installs `hooks/hooks.json` and `.mcp.json` whose command is the bare name `yoetz`. When Codex later runs a hook or starts the plugin MCP server, it resolves that name on its `PATH`. A test instance, an older install, or any earlier `yoetz` executable becomes the hook process and the long-running server child. That child can answer `service_incompatible` and advise a restart that replaces the live singleton. External `codex mcp add` no longer does this; the plugin tree still does.
- Suggested fix and test: Render Codex plugin hooks and `.mcp.json` through the same `resolve_yoetz_launcher` / `installed_launcher` pair the other native carriers use. Put the absolute argv in the marker and the artifact digest. Keep recognizing a legacy bare command as owned-but-drifted so removal and migration still work, and do not treat it as the desired post-install state. Test: render `render_plugin_tree` and `render_plugin_install_tree` with a fake scripts-directory launcher and assert every `command` string is absolute (or `python -m yoetz` with an absolute interpreter) and contains no bare leading `yoetz`. Add a marketplace activation fixture that fails if cache members still contain `command":"yoetz"` or a hook command that starts with `yoetz `.
- Confidence: high
- Related tests: `tests/unit/adapters/test_codex_plugin.py` (hook command shape), `tests/unit/adapters/test_codex_marketplace.py` (cache member digest), `tests/unit/adapters/test_codex_mcp.py` (absolute external registration). The external-registration tests do not cover this plugin tree.

## H-02

- Severity: medium
- Category: path traversal and permission checks when writing host config
- Title: Host admission accepts non-canonical project roots and writable directories
- File: `src/yoetz/adapters/integrations/host_admission.py:310-318`, `src/yoetz/adapters/integrations/host_admission.py:971-996`; CLI passes the path through at `src/yoetz/cli/host_admission.py:294`
- Evidence: `_validated_root` requires an absolute path whose final component is a directory and not a symlink. It does not reject `..` components, does not compare `absolute()` with `resolve()`, and does not check `st_uid` or mode `0o022`. `_write_private` then creates `.claude`, `.codex`, or `.cursor` if missing and replaces the host file there. The preview JSON deliberately omits the path (`test_errors_and_reports_never_carry_paths_or_contents`). Sibling writers do the missing checks: `cursor_project_mcp._path` rejects `..` (`cursor_project_mcp.py:77-79`); `codex_marketplace._validated_project` rejects a root whose absolute path differs from `resolve()` and rejects group/world-writable or other-uid directories (`codex_marketplace.py:432-454`); Cursor and Claude plugin targets walk ancestors and refuse a symlink (`cursor_integration.py:930-959`, `claude_code_integration.py:975-1008`).
- Failure scenario: `yoetz integrate <host> admission grant --project-root /work/repo/../../other --accept ...` writes `.claude/settings.local.json`, `.codex/config.toml`, or `.cursor/permissions.json` under the normalized directory, not under `/work/repo`. An ancestor symlink is also accepted because `Path.is_symlink()` only inspects the final component. A group- or world-writable project root is accepted, so another local user who can write that directory can replace the admission file after Yoetz publishes it. The operator-facing preview shows surface names and digests, not the path that will be written, so the digest does not show the escape.
- Suggested fix and test: Use the marketplace rule: after absolutizing, refuse when `root.absolute() != root.resolve()`, when any ancestor is a symlink, when the owner is not the euid, or when mode includes `0o022`. Apply the same checks to the parent of each surface immediately before `mkstemp` / `os.replace`, using `lstat` rather than `exists`. Keep path strings out of error tokens; the refusal reason can stay `target_unsafe`. Tests: a root containing `..` that normalizes outside `tmp_path` raises `TARGET_UNSAFE` and creates no file; a root reached through a symlinked ancestor does the same; a `0o707` directory does the same; the existing grant/revoke cases on a private real directory still pass.
- Confidence: high
- Related tests: `tests/unit/adapters/test_host_admission.py::test_relative_or_missing_project_root_is_unsafe` covers only a relative path and a missing directory.

## H-03

- Severity: medium
- Category: path traversal when writing the project plugin tree
- Title: Codex project plugin install does not canonicalize the project root
- File: `src/yoetz/adapters/integrations/codex_plugin.py:351-376`, `src/yoetz/adapters/integrations/codex_plugin.py:488-562`; CLI preserves the lexical path at `src/yoetz/cli/codex_plugin.py:55-61` with the comment that the adapter will reject a symlink
- Evidence: `_validated_project` rejects a final-component symlink, filesystem root, and `$HOME`, and it checks owner and `0o022`. It never compares `absolute()` to `resolve()`. `_validated_plugin_parent` then creates `.agents/plugins` under that path and `install_plugin` swaps in the tree with `os.replace`. `codex_marketplace._validated_project` (`codex_marketplace.py:441-454`) refuses the same class of root because `root.absolute() != root.resolve()`. The CLI comment says the lexical path is preserved so the adapter can reject a symlinked root; that check does not see ancestor symlinks or `..`.
- Failure scenario: A project root of `/work/repo/../../elsewhere` is absolute, its final component is a real directory, and install writes `.agents/plugins/yoetz` outside `/work/repo`. The same happens when an ancestor of the named root is a symlink. Marketplace activation of that same `IntegrationTarget` would have refused the root; the plugin installer accepts it.
- Suggested fix and test: Share `_validated_project` with the marketplace helper (resolve equality plus the existing owner and mode checks) and call it from both `install_plugin` and `inspect_plugin`. Test that a root with a `..` component and a root whose parent is a symlink raise `TARGET_UNSAFE` and leave no `.agents` directory outside the fixture.
- Confidence: high
- Related tests: plugin install tests under `tests/unit/adapters/` that use `tmp_path` directly; none of the searched tests assert `..` or ancestor-symlink rejection for `install_plugin`.

## H-04

- Severity: medium
- Category: missing uninstall / reverse path
- Title: Project plugin install has no inverse, and Codex removal leaves the tree
- File: `src/yoetz/adapters/integrations/codex_plugin.py:488` (`install_plugin` is the only mutator; there is no remove), `src/yoetz/application/codex_plugin.py:72-88`, `src/yoetz/cli/setup.py:1454-1460`, `src/yoetz/adapters/integrations/codex_marketplace.py:2873-2905`
- Evidence: `CodexPluginService` exposes `preview`, `inspect`, and `install`. `install_plugin` creates `.agents/plugins/yoetz`. `apply_removal` removes the Codex inventory entry, marketplace registration, exact config tables, and managed cache versions. It then re-reads `skill_tree_state` and fails if that state changed (`codex_marketplace.py:2903-2905`), which means removal is specified not to delete the project skill/plugin source. After a successful removal, `inspect_activation` still treats a present project tree as installed (`codex_marketplace.py:923-984`), so the state stays `installed_not_activated` rather than `not_installed`. The CLI surface for this host is `preview`, `status`, and `remove` (`src/yoetz/cli/codex_plugin.py:34`) and `remove` calls `apply_removal` only.
- Failure scenario: First-run setup writes the project plugin tree, then the operator runs the supported Codex plugin remove. Registration, `config.toml` tables, and the cache are cleared, but `.agents/plugins/yoetz` remains on disk with hooks that Codex can still discover as a local plugin source. There is no adapter call that deletes only that managed tree and restores the previous directory. A later activation sees the leftover tree and will not report a clean absence.
- Suggested fix and test: Add a digest-bound remove next to `install_plugin` that deletes only a marker-valid tree (same whole-directory swap used on install, with the safe-removal checks already used for the cache) and wire it from setup rollback and from `apply_removal` when the project tree is the managed render. Leave foreign or user-modified trees as `remove_refused`. Test: install into a fixture, run the new remove, assert the `yoetz` directory is gone and a sibling unrelated plugin directory is untouched; assert `inspect_plugin` reports `ABSENT`; assert a tree with one edited file is refused and left in place.
- Confidence: high
- Related tests: marketplace removal tests that assert cache and config bytes; they do not assert removal of `.agents/plugins/yoetz`.

## H-05

- Severity: medium
- Category: command execution from host config
- Title: Cursor status executes a self-hashed marker launcher that is not this install
- File: `src/yoetz/adapters/integrations/cursor_integration.py:1056-1122`, `src/yoetz/adapters/integrations/cursor_integration.py:2006-2036`, `src/yoetz/adapters/integrations/launcher_probe.py:96-116`; invoked from `status_cursor_plugin` at `cursor_integration.py:1806-1832` and from `src/yoetz/cli/cursor_integration.py:249-250`
- Evidence: `_valid_marker` accepts any absolute launcher tuple that passes `valid_launcher` as long as `marker_digest` equals the canonical hash of the marker body. That hash is not a signature. `_launcher_status` sets `executable` to `drifted` when the marker launcher differs from this runtime, then still calls `OsLauncherProbe.probe` whenever the path is an executable file. The probe runs `[*launcher, "version", "--json"]` with `HOME`, `PATH`, `TMPDIR`, and the Windows profile variables copied into the child environment. No shell is used, and arguments are a list, so this is not shell injection. It is execution of a path taken from host state during a command documented as status.
- Failure scenario: A process that can write the user Cursor plugin directory can plant a marker-consistent tree whose `yoetz_launcher` is an absolute executable it controls. The next `yoetz integrate cursor plugin status` (or any status call that uses `OsLauncherProbe`) starts that executable as the user, with the user's home and `PATH` in the environment, even though the launcher does not match this installation. A neighboring Yoetz channel is the intended case. Nothing in the probe requires the file to be a Yoetz console script, an owner-only path, or the recorded install.
- Suggested fix and test: Before `probe`, require the same path checks `installed_launcher` already uses for parents: no symlink, not group/world-writable, owned by the euid or root, basename `yoetz`, and a size cap. If those checks fail, return `UNOBSERVED_LAUNCHER_IDENTITY` and keep `executable="drifted"` without spawning. Pass an environment that does not include `HOME` unless `version --json` truly needs it. Test: a marker whose launcher is a mode-`0755` script outside an owner-only directory is reported `drifted` and the script is not executed (have the script touch a sentinel file). A launcher that is this runtime's console script is still probed.
- Confidence: medium. The probe is an intentional identity check for a drifted channel (issue #468). The gap is that the spawned path is not constrained to a Yoetz-shaped install before execution.
- Related tests: launcher-probe unit tests that feed `FixedLauncherProbe` never spawn; `OsLauncherProbe` tests should be checked for a negative case that a non-install absolute path is not run. None was found in the adapter read.

## H-06

- Severity: low
- Category: discovery-scan performance
- Title: Cursor runtime status reads every process on Linux before it applies its cap
- File: `src/yoetz/adapters/integrations/cursor_mcp_runtime.py:32`, `src/yoetz/adapters/integrations/cursor_mcp_runtime.py:389-449`; called from `status_cursor_plugin` (`cursor_integration.py:1822-1827`)
- Evidence: `_MAX_PROCESSES` is 64, but that limit is applied only after a process has already been classified as a Yoetz MCP serve. The first loop `iterdir`s all of `/proc`, then reads `comm` and `status` for every numeric pid. The second loop reads `cmdline` for every pid until 64 matches exist. On a machine with no matching processes it reads every pid. There is no deadline. The Darwin branch is two `ps` calls with a 2 second timeout (`cursor_mcp_runtime.py:471-492`). Codex discovery is separately capped at 16 version probes (`codex_discovery.py:30`, `codex_discovery.py:185-187`).
- Failure scenario: `status` on a host with a large process table (many containers, short-lived jobs, or a slow `/proc`) blocks the Cursor plugin status command on tens of thousands of small reads, on the UI/CLI path, before it can report runtime activation. A hung or fuse-backed `/proc` entry stalls the walk with no timeout.
- Suggested fix and test: Cap the number of pids inspected (for example the same order as `_MAX_PROCESSES` times a small constant), stop the walk at a monotonic deadline similar to the Darwin 2 second timeout, and read `cmdline` only after `comm` matches a Yoetz or Cursor helper name. Test with a fake `/proc` of more than the cap directories and assert the number of `cmdline` reads and that a slow `status` file does not exceed the deadline.
- Confidence: high
- Related tests: `tests/unit/adapters/test_cursor_mcp_runtime.py` if present uses fixed process snapshots; the `OsCursorMcpProcesses` Linux walk has no bound test.

## Coverage notes

Command injection via `shell=True` was not found in this set. Subprocess calls use argument lists. The macOS presence prompt and the Linux PAM account name are argv elements; the account is constrained to a short printable ASCII name with no spaces.

Symlink handling on the Codex cache, Cursor project MCP, hook spool, and startup-gate paths is descriptor- or `O_NOFOLLOW`-based. Residual same-uid rename windows are disclosed in admission and MCP previews (`host_config_not_compare_and_swap`) and were not re-filed.

Secrets are not written into the host files these adapters generate. Isolation is a path (`YOETZ_ISOLATED_ROOT`) only. Admission rewrites can re-emit unrelated keys already present in a Claude or Cursor JSON file, including an `env` object the user stored there; that is preservation of existing file contents, not Yoetz inserting a secret.


---

# Local observation capture and import

Source notes: `12-observation-local.md`.

# Observation local audit

Read-only review of the observation store, Codex session-stream reader, rollout/session importers, capability harness, and the CLI call sites that feed them. Line numbers refer to the tree at audit time. Findings below are from the source as read; searches that did not produce a defect are listed at the end.

## C-01

- Severity: high
- Category: duplicate events
- Title: Stream replay after a failed admission mints a second source identity
- File: `src/yoetz/adapters/integrations/codex_session_stream.py:994-1007`, `src/yoetz/adapters/integrations/codex_session_stream.py:1438-1511`, `src/yoetz/adapters/integrations/codex_session_stream.py:1930-2084`, `src/yoetz/adapters/integrations/observation_local.py:1933-1945`, `src/yoetz/adapters/integrations/observation_local.py:7776-7817`
- Evidence: `envelope_from_stream_record` folds `record.byte_end` into `source_identity`. That field is the chunk-relative end from `split_codex_rollout_jsonl_chunk` (`line.byte_end` is an offset inside the bytes just read), then copied onto the positioned record at `codex_session_stream.py:1497-1504`. The absolute file position lives only on `ObservationCursor.byte_position` (`byte_position + consumed`). `_reconcile_session_stream_path` calls `store.ingest(envelope)` before `commit_selected_admission`. On admission failure it sets `overflow` and `break`s before `committed_cursor = envelope.cursor`, so the durable stream cursor stays on the previous line. The ingest is not rolled back: admission failure is a `False` return, and `reconcile_session_stream_path` commits the batch on a normal return (`codex_session_stream.py:1769-1776`). The next pass therefore starts at the failed line. That line's chunk-relative `byte_end` changes because the chunk no longer contains the preceding lines, so the digest changes. `_dedup_key` includes `source_identity`. `ObservationCursor.is_stale_relative_to` is strict (`domain/observation.py:1242-1251`), so an equal absolute cursor is not a stale rejection. The replay is accepted as a second envelope.
- Failure scenario: One reconcile reads line A and line B from the same chunk. A is admitted. B is ingested, then `commit_selected_admission` returns false (outbox cap, summary refusal, or a `ProtocolValueError` / `OSError` caught at `2039-2047`). The batch persists B's envelope and a stream cursor still sitting on A. The following reconcile reparses B alone, builds a different `source_identity`, and ingests it again. `selection_observed_count` increments twice. `list_envelopes` retains both. A later successful admission delivers the second identity while the first remains as an unadmitted local copy of the same source line.
- Suggested fix and test: Put only stable coordinates in the identity: `cursor.byte_position`, `cursor.event_position`, and `cursor.source_generation`. Do not hash chunk-relative `byte_start` / `byte_end`. Alternatively, do not call `ingest` until admission has succeeded, and roll the batch back when admission returns false so a replay hits the original dedup key. Test: build a two-line rollout fixture, force `commit_selected_admission` to return false on the second line only, reconcile twice, and assert a single `source_identity` and a single envelope. A second case should assert that a same-absolute-cursor replay is `duplicate` even when the second read starts at the line boundary.
- Confidence: high
- Related tests: `tests/unit/adapters/test_codex_session_stream.py` covers cursor advance and pairing. No test forces admission failure mid-chunk and then reconciles again, so this replay is unguarded.

## C-02

- Severity: high
- Category: incorrect redaction before persistence
- Title: Codex session JSONL import stores command text, output, paths, and tool arguments with no secret redaction
- File: `src/yoetz/adapters/importers/codex_jsonl.py:957-1028`, `src/yoetz/adapters/importers/codex_jsonl.py:1265-1294`, `src/yoetz/adapters/importers/codex_plan.py:238-247`, `src/yoetz/adapters/memory/importer.py` (`event_draft_bytes`)
- Evidence: `_action_template` copies `item["command"]` onto the action payload and, for a terminal command, copies `item["aggregated_output"]` into `summary` when it fits in `MAX_TEXT_BYTES`. File-change paths, the web-search query, MCP `server`/`tool` (and, via `_item_identity`, MCP `arguments`), and collab `prompt` are likewise carried in the template. `materialize_codex_mapping` places `command` on `ActionRecordedPayload` and `summary` on `ResultRecordedPayload` with type checks only. `CodexPlanImporter.prepare` serializes those drafts with `event_draft_bytes` into the import-plan object. There is no call to `redact_sensitive_content` or `prepare_persisted_plaintext` anywhere under `src/yoetz/adapters/importers/`. `sanitize_codex_argv` redacts argv values, and it is not applied to `command_execution.command`. The sibling rollout parser does redact every string before the record is frozen (`codex_rollout_jsonl.py:379` and `491-530`).
- Failure scenario: An imported `command_execution` line whose command or `aggregated_output` contains `sk-proj-…`, `ghp_…`, or `AWS_SECRET_ACCESS_KEY=…` is written into the import-plan object as the action `command` and the result `summary`. The same bytes, read by the rollout parser, would have those spans replaced before any record exists.
- Suggested fix and test: Run command, output, query, prompt, path, and MCP argument strings through `prepare_persisted_plaintext` before they are copied into a template. Withhold the field (gap `source_text_not_represented`, empty command only if the schema allows it, otherwise a redacted placeholder plus an explicit gap) when `persist` is false, including the 128-finding cap. Do not treat a redacted `__repr__` as proof. `test_command_mapping_materializes_stable_import_candidates_without_leakage` only asserts the canary is absent from `repr(prepared)`, and `CodexPreparedMapping.__repr__` is hard-coded to `CodexPreparedMapping(<redacted>)` (`codex_jsonl.py:346-347`). Assert instead on `prepared.event_drafts[-2].payload.command` and `prepared.event_drafts[-1].payload.summary`, and on `event_draft_bytes`, that a planted `sk-proj-` span is `[REDACTED]` or withheld.
- Confidence: high
- Related tests: `tests/unit/adapters/test_codex_jsonl.py` (`test_command_mapping_materializes_stable_import_candidates_without_leakage`), `tests/capability/test_codex_jsonl_import.py`, `tests/unit/adapters/test_codex_rollout_jsonl.py` (rollout side does assert `[REDACTED]`).

## C-03

- Severity: high
- Category: incorrect redaction before persistence
- Title: Observation capture uses the scanner that keeps secrets past the 128-finding cap
- File: `src/yoetz/observability/privacy.py:38`, `src/yoetz/observability/privacy.py:283-288`, `src/yoetz/observability/privacy.py:359-374`, `src/yoetz/observability/privacy.py:461-477`, `src/yoetz/adapters/importers/codex_rollout_jsonl.py:505-522`, `src/yoetz/cli/observe_hooks.py:1275-1390`
- Evidence: `_MAX_SCAN_FINDINGS` is 128. `_append_finding` stops appending once that length is reached, and the pattern loop only `break`s the current match iterator, so later matches are never recorded. `prepare_persisted_plaintext` treats a full finding list as unsafe and returns `persist=False` with empty content. The comment there states that reaching the cap does not prove there is no later match, so persistence must withhold rather than redact only the visible prefix. `redact_sensitive_content` does not check the cap. It replaces the returned spans and returns the remainder, with `detected=True`. `_redact_json_tree` calls `redact_sensitive_content` on every string, then freezes the tree into `CodexParsedRecord.value`. Structural extraction then copies any surviving string that passes `_token` into `tool_name`, `tool_call_id`, `subagent_id`, and the other allowlisted fields (`codex_session_stream.py:875-963`), which `ingest` writes into the workspace JSON. `_visible_content_chunks` uses the same function on tool output, prompts, diffs, and file content, sets `ObservationContentChunk.redacted` from `detected`, and the hook forwards those chunks for capture (`observe_hooks.py:3600-3620`).
- Failure scenario: A tool result or rollout line contains 128 credential-shaped assignments and then another `ghp_` or `sk-proj-` value. The first 128 become `[REDACTED]`. The later secret is unchanged. For a hook with content capture authorized, that tail is inside a content chunk marked `redacted=True` and is handed to the drain. For a rollout line, a later string that is itself a single token of at most 128 characters from the `_token` alphabet (for example a `name` or `call_id` that is a GitHub token, after 128 earlier secrets in the same JSON value or in earlier strings of the same line) is stored on the observation envelope in the clear. Key redaction can also collapse two object keys onto one string and raise `duplicate_object_key` (`codex_rollout_jsonl.py:517-520`), which the parser turns into `MALFORMED` and the stream records as an opaque line. That drops the semantic event rather than leaking it.
- Suggested fix and test: Make `_redact_json_tree` and `_visible_content_chunks` call `prepare_persisted_plaintext`. If `persist` is false, do not freeze the original string and do not emit the chunk; record a truncation or unsupported gap instead. Add a regression that plants 128 `AWS_SECRET_ACCESS_KEY=…` lines plus one later `sk-proj-` token and asserts the token is absent from the frozen rollout record, from the structural envelope, and from the content chunk bytes. `tests/unit/observability/test_privacy.py::test_prepare_persisted_plaintext_withholds_at_finding_capacity` already locks the safe behavior for the other API and does not call `redact_sensitive_content`.
- Confidence: high
- Related tests: `tests/unit/observability/test_privacy.py`, `tests/unit/adapters/test_codex_rollout_jsonl.py`, `tests/integration/application/test_native_capture_pipeline.py`.

## C-04

- Severity: high
- Category: lost durable observation state
- Title: A failed or oversized workspace read is treated as an empty store and then overwritten
- File: `src/yoetz/adapters/integrations/observation_local.py:500-512`, `src/yoetz/adapters/integrations/observation_local.py:8319-8345`, `src/yoetz/adapters/integrations/observation_local.py:2167-2233`
- Evidence: `_read_bytes` returns `None` when the path is missing, is a symlink, `stat` or `read_bytes` raises `OSError`, the size is 0, or the size is greater than `maximum`. It does not distinguish those cases. `_load` uses `maximum=_MAX_LEGACY_STATE_BYTES` (36 MiB). `None`, a `strict_json_parse` `ProtocolValueError`, or a non-object document all return a brand-new `_WorkspaceState()`. The in-function `return` happens before the stat-matched cache update, so the blank state is not cached, but every mutator that then calls `_save` persists it. Contrast `_runtime_gate_facts`, which raises `STORAGE_UNSAFE` for an unsafe, oversize, or unreadable gate and does not replace the marker except through the explicit setter.
- Failure scenario: The workspace file is momentarily unreadable (`EIO` on `is_file` / `stat` / `read_bytes`), larger than 36 MiB, or rejected by `strict_json_parse`. The next `ingest`, `grant_consent`, `enqueue_outbox`, or stream reconcile loads a blank state and `_save`s it through `_atomic_write`. Consent, outbox rows, dedup keys, stream cursors, and session bindings that were in the previous file are replaced. There is no `observation_storage_corrupt` gap, because the corrupt bytes were never loaded.
- Suggested fix and test: If the path exists, or `stat` succeeds, or the bytes fail `strict_json_parse`, raise `PublicOperationError(STORAGE_UNSAFE)` and do not `_save`. Reserve the empty `_WorkspaceState()` for a confirmed absence (`FileNotFoundError` on `O_NOFOLLOW` open). Add a test that writes a valid workspace, replaces the file with `b"{"` or a file larger than `_MAX_LEGACY_STATE_BYTES`, calls `ingest` or `grant_consent`, and asserts the original bytes are unchanged and the call fails closed.
- Confidence: high on the control flow. The practical trigger is a read error or a document the strict parser rejects, not the steady-state writer, which caps encoded state at 16 MiB (`_MAX_EXPANDED_STATE_BYTES`).
- Related tests: `tests/unit/adapters/test_observation_state_bounds.py`, `tests/unit/application/test_observation_coordinator.py` (corrupt-session quarantine assumes the file still parses).

## C-05

- Severity: medium
- Category: lost spool records / session-identity break
- Title: Any failed key read replaces `key-material.bin` and orphans existing commitments
- File: `src/yoetz/adapters/integrations/observation_local.py:2094-2102`, `src/yoetz/adapters/integrations/observation_local.py:500-512`, `src/yoetz/adapters/integrations/hook_spool.py:107-109`, `src/yoetz/adapters/integrations/hook_spool.py:431-447`
- Evidence: `key_material` holds the store lock, then `_read_bytes(..., maximum=_KEY_BYTES)` where `_KEY_BYTES` is 32. Anything other than exactly 32 bytes — `OSError`, symlink, empty, short, or longer — falls through to `os.urandom` and `_atomic_write`, which replaces the file. The instance then caches that new material in `_cached_key_material`. `session_commitment_from_codex_id` and workspace commitments are HMACs of this key. `HookSpool._key_material` only reads. It accepts 16 to 64 bytes and refuses a longer read (`os.read(..., 65)` then `len > 64`). A store-side replacement renames the commitment. Spool files are `{digest}.jsonl`. `has_pending` / `claim` look up the new digest, so the old file is never claimed. `HookSpool.append` also returns false when the key cannot be read (`hook_spool_key_missing`), so a hook that races the first key creation drops the record instead of creating the key. The interprocess lock does serialize two store creators; this is not a double-create race. It is a destructive repair on a failed read.
- Failure scenario: `key-material.bin` exists and is 32 bytes. One `stat` or `read_bytes` raises `OSError`. `key_material` writes a new key. Session bindings already stored under the old HMAC no longer match `session_commitment(codex_session_id)`. Pending spool lines for the old workspace digest sit on disk with `has_pending` false for every workspace the new key names. Those lines are not replayed.
- Suggested fix and test: Open the key with `O_NOFOLLOW`, require a regular 32-byte file owned by the effective uid, and return those bytes. On `EIO`, short read, or oversize, raise `STORAGE_UNSAFE` and leave the file untouched. Create the key only when `open` returns `ENOENT`. Test: write a known 32-byte key, monkeypatch `Path.read_bytes` or `os.read` to raise `OSError` once, call `key_material`, and assert the file bytes are unchanged. A second test should append a spool line, replace the key, and assert `pending_workspaces` still reports the original digest or that the reader refuses to switch keys.
- Confidence: high on the overwrite. Medium on how often `OSError` hits a local disk; the consequence if it does is an unrecoverable commitment split.
- Related tests: `tests/unit/application/test_observation_coordinator.py` (asserts the key length is 32 after normal use), `tests/unit/adapters/test_hook_spool.py` (writes the key itself).

## C-06

- Severity: medium
- Category: lost read-protection accounting
- Title: A failed or unsuccessful `consume_read_protection` still advances the stream cursor
- File: `src/yoetz/adapters/integrations/codex_session_stream.py:2048-2078`, `src/yoetz/adapters/integrations/observation_local.py:3049-3113`
- Evidence: After a protected stream output is admitted, the reader calls `consume_read_protection` and ignores both a `False` return and `AttributeError`, `OSError`, `ProtocolValueError`, `TypeError`, and `ValueError` (`pass`). It then sets `committed_cursor = envelope.cursor`. The adjacent self-observation path does the opposite: if `note_selection_omission` raises, it sets `delivery_blocked` and `break`s, with the comment that the source cursor must not pass an accepted input whose accounting did not commit (`2068-2072`). Because the cursor moves, the next reconcile does not see this line again, so consume is not retried. `consume_read_protection` returns `False` when the attempt does not match, and it can raise `OSError` from `_save` when the caller is not already inside a batch that turns `_save` into a dirty flag. The hook path at `observe_hooks.py:3706-3709` calls the same method and also ignores a failure, but a later hook for that same event can retry; the stream cursor cannot.
- Failure scenario: A protected `function_call_output` is admitted and the stream cursor is committed. Consume returns false or raises. The protection row stays active until its TTL. It can still match a later read in that session, past `MAX_READ_PROTECTION_COUNT` accounting, or it simply never records that this post consumed the grant. The operator's "protect the next read" applies to a different call than the one that was admitted.
- Suggested fix and test: Treat a false return or any exception from consume like `note_selection_omission`: set `delivery_blocked`, do not move `committed_cursor`, and leave the partial in place. Test with a store whose `consume_read_protection` raises `OSError` once and then succeeds: the first reconcile must keep the pre-line stream cursor, and the second must consume exactly once.
- Confidence: high that the cursor moves; medium that `False` is common, because a matching probe usually consumes. The asymmetry with the omission path is deliberate-looking only in the omission comment, not in the consume path.
- Related tests: `tests/unit/adapters/test_codex_session_stream.py` (read-protection stamping). No test asserts cursor behavior when consume fails.

## C-07

- Severity: medium
- Category: unbounded memory read
- Title: Codex artifact capture reads an entire executable with no byte cap
- File: `src/yoetz/adapters/integrations/codex_capability_harness.py:61-84`
- Evidence: `capture_codex_artifact_identity` checks `is_symlink` and `is_file`, then `path.read_bytes()` with no maximum. The check and the read are separate operations, and `read_bytes` follows a symlink that appears after the check. `discover_codex_capability_artifact` calls this on every discovered binary and continues only after `OSError` or `ValueError`. It does not catch `MemoryError`. `codex_capability_cells.py` is a fixture table and does not read files.
- Failure scenario: Discovery returns a regular file of multi-gigabyte size, or a path that is replaced with a symlink to one between `is_symlink()` and `read_bytes()`. The process allocates the whole file to hash it. A transcript-sized cap is not involved here; this is the unbounded read in the capability path.
- Suggested fix and test: Open with `O_NOFOLLOW`, require a regular file, and hash in fixed-size chunks while refusing `st_size` above a stated executable bound before any full read. Test a file larger than the bound and a symlink; both must raise `ValueError` without reading the target to completion.
- Confidence: high
- Related tests: none under `tests/` reference `capture_codex_artifact_identity` by the searches run for this audit.

## C-08

- Severity: medium
- Category: path escape at the CLI boundary
- Title: Manual stream reconcile ingests a file the locator rejected
- File: `src/yoetz/cli/observe.py:1931-1975`, `src/yoetz/adapters/integrations/codex_session_stream.py:385-496`
- Evidence: `reconcile_session_stream` rejects a symlink, then asks `CodexSessionStreamLocator.resolve` for a path that must sit under `{codex_home}/sessions`, be owner-matched, contain `session_id` in the filename (`session_id not in resolved.name`), and pass the suffix and first-byte checks. When `resolve` returns `None`, the CLI still calls `reconcile_session_stream_path` with the original `Path(session_file)`. That reader opens the path directly (`SessionStreamReader.advance` → `path.open`). The comment at `1967-1968` describes this as explicit recovery. It is still a second entry point that does not repeat `_is_beneath` or `_owner_safe`. The hook caller does not have this fallback: `reconcile_session_stream` returns `resolved: False` when the locator returns none (`codex_session_stream.py:1742-1749`), and `observe_hooks.py:3754-3760` uses that function. A separate CLI defect on the unmapped non-`rollout-` path splits the stem on the last hyphen (`observe.py:1948-1951`) and then uses that suffix as the session id. Combined with the locator's substring test, a short suffix binds the file if the suffix occurs anywhere in the name.
- Failure scenario: `yoetz` reconcile is pointed at a JSONL path outside the Codex session root, or at a file the locator refused because the owner or the directory walk did not match. The process still parses it and ingests envelopes into the consented workspace, under a session commitment derived from the filename. That is the path-confinement check being skipped, not a zip slip inside the parsers.
- Suggested fix and test: If `resolve` returns `None`, exit with the existing `session_file_unreadable` or a distinct `session_path_rejected` and do not call `reconcile_session_stream_path`. For recovery of a moved file, require a separate flag and still refuse symlinks, non-regular files, and paths whose real device/inode is not the opened descriptor (`O_NOFOLLOW`). Test: a regular JSONL outside the Codex home must not create envelopes.
- Confidence: high that the fallback reads the rejected path. The comment shows it is intentional recovery, so this is a confinement hole in that recovery mode rather than an accidental extra call.
- Related tests: CLI observation tests were not found locking "locator miss must not read". `tests/unit/adapters/test_codex_session_stream.py` locks the locator itself.

## C-09

- Severity: low
- Category: coverage honesty
- Title: Spool pending state disappears from observation status when the spool probe throws
- File: `src/yoetz/adapters/integrations/observation_local.py:9063-9071`
- Evidence: `_current_gaps` says a legacy hook has not reached the outbox until `HookSpool.has_pending` is false, and it adds `SOURCE_LAG` in that case. The probe is wrapped in `contextlib.suppress(Exception)`. `has_pending` uses `Path.exists`, which raises `OSError` on some filesystem failures. Any such error, or a constructor failure, leaves the gap unset.
- Failure scenario: The spool directory is unreadable or `exists` raises. Status omits `source_lag` and can report the workspace caught up while `{digest}.jsonl` or `{digest}.draining` still holds records.
- Suggested fix and test: On `OSError` or `PathSafetyError`, add `SOURCE_LAG` (or `observation_storage_corrupt`) instead of swallowing. Test with `has_pending` raising `OSError` and assert the gap is present.
- Confidence: high
- Related tests: `tests/unit/adapters/test_hook_spool.py`. Status tests that only cover a readable empty spool will not catch this.

## C-10

- Severity: low
- Category: lost spool records
- Title: Spool append can spin if `os.write` returns 0, and a malformed line is skipped forever
- File: `src/yoetz/adapters/integrations/hook_spool.py:128-131`, `src/yoetz/adapters/integrations/hook_spool.py:352-370`, `src/yoetz/adapters/integrations/hook_spool.py:409-415`
- Evidence: The append loop is `while written < len(line): written += os.write(...)` with no `written == 0` check. `_atomic_write` in the observation store does raise on `written <= 0` (`observation_local.py:483-486`). `_read_batch` advances `next_offset` to `handle.tell()` before `_parse_line`. `_parse_line` returns `None` on `except Exception` around `strict_json_parse`, and on a workspace mismatch, without putting the line back. `_commit_batch` then persists that offset, so the line is never retried.
- Failure scenario: A zero-length write on the spool descriptor holds `_workspace_lock` until the hook is killed, which blocks other appends and claims for that workspace. Separately, one corrupt or wrong-commitment line is permanently skipped when the claim commits. Valid neighbors survive. This is bounded discard of a bad line, not deletion of the whole spool. `claim` on an unparseable offset file yields an empty batch and leaves the draining file in place (`hook_spool.py:187-189` and `304-306`), which stalls that workspace until the offset file is repaired.
- Suggested fix and test: Raise `OSError` when `os.write` returns a non-positive count. For offset files, quarantine a corrupt `.offset` to 0 only when the draining inode is unchanged, and surface a gap rather than yielding an empty success forever. A test can use a fake file object whose `write` returns 0 and assert the append raises without looping; the production path needs a pipe or a mocked `os.write`.
- Confidence: medium for the zero-write spin (rare on a regular file, definite if it happens). High that a failed `_parse_line` is skipped once the batch commits.
- Related tests: `tests/unit/adapters/test_hook_spool.py`.

## Parser differential (recorded, not a separate defect id)

`codex_jsonl._parse_json_line` accepts a finite float, then `freeze_json` rejects the whole line as `json_profile_unsupported` (`codex_jsonl.py:524-541` and `799-803`; `protocol/canonical.py` `float_forbidden`). `codex_rollout_jsonl._redact_json_tree` replaces every finite float with `None` before validation so the line still maps (`codex_rollout_jsonl.py:494-512`). That split is the #754 behavior called out in the rollout comment. It is a real differential for the same JSON bytes. It does not by itself leak data. The secret-handling differential is C-02 and C-03.

Both parsers cap a single document at 1 MiB per line, 4 MiB per buffer, 20_000 lines, and JSON depth 64, and both reject duplicate keys in `_pairs`. `json.loads` runs before the depth walk; `RecursionError` is caught and becomes `malformed_json`. A 1 MiB line cannot amplify the way a zip bomb does. No `zipfile`, `tarfile`, `pickle`, `yaml.load`, or `eval` is present in the audited modules.

`SessionStreamReader.advance` reads at most one chunk plus a read-ahead bounded by `ROLLOUT_MAX_LINE_BYTES + 1` (`codex_session_stream.py:1246-1281`). An unterminated line between `_MAX_STREAM_PARTIAL_BYTES` (262_144) and 1 MiB is not stored as a partial (`observation_local.py:4508-4511`); the cursor stays put and the next pass rereads the file. That matches the comment at `observation_local.py:178-193`. It stalls later lines until a newline arrives. It does not drop a terminated line.

## Searches with no matching defect in this scope

- `shell=True`, `subprocess`, `eval`, `pickle`, `yaml.load`: no uses in the audited observation, importer, stream, or capability modules. `observe_hooks.py` has no `subprocess` call; a comment near `4065-4071` says a `codex mcp get` probe was deliberately not added on the hook path.
- Hook command injection from observation payload: structural extraction keeps a token allowlist (`observe_hooks.py:687-823`). Content fields are not passed to a shell in these files.
- `observation_admission.py`: bounds are `SUMMARY_MAX_CALLS` 16, `SUMMARY_MAX_INPUTS` 32, `MAX_BUFFERED_INPUTS` 256. The summary builder rejects cross-lane tuples, stale cursors, and open pres. No separate persistence or path bug showed up in that file. The duplicate in C-01 is in the stream caller, which commits `ingest` when admission returns false.
- `codex_capability_cells.py`: constants and fixture ids only.

## CLI notes that are not store bugs

`observe_hooks.py:3737-3760` suppresses every exception around `reconcile_session_stream`. A deterministic exception there skips the stream for that hook and relies on a later hook to retry. That is silent, and it is not the C-01 identity bug. `3727-3732` likewise suppresses `note_session_end`, so a failed end mark leaves the session looking live until a later attempt. `handle_spool` (`5476-5513`) copies the payload and then lets `HookSpool.append` keep only `_SAFE_FIELDS`, so the full host payload is not what hits disk. Append still returns false with no retry when the record is over 8 KiB or the key is missing (C-05).


---

# Protocol and domain

Source notes: `13-protocol-domain.md`.

# Protocol and domain audit

Read-only review of `src/yoetz/protocol/`, the non-observation domain modules (`events.py`, `findings.py`, `receipts.py`, `values.py`, `coordination.py`, `host_lineage.py`, `privacy.py`), and spot checks of `schemas/` where the wire shape and the code disagree. Schemas are treated as the wire authority (ADR-002 still says sequences are canonical integer strings; where a checked-in schema says otherwise, that schema is what a peer will implement).

No source was modified. No live Yoetz ledger was started.

## Executive summary

The closed-model and closed-payload gates are real: ordinary pydantic models set `extra="forbid"`, and `decode_payload` rejects unknown keys. Identifier minting is CSPRNG UUIDv4 with unique 4-character prefixes, and the operation-status page refuses replay fields on `state="quarantined"`. Those hunts did not produce a defect.

The defects that did show up are contract splits on the current session and lineage schemas, a bool-shaped priority that `Finding` already rejects and `ChildFindingSnapshot` does not, pointer admission that is stricter than the pointer schema, and schema validation that is rebuilt and walked twice per public model. Public protocol errors do not interpolate exception text; the schema validator does the opposite and relabels internal failures as `schema_instance_invalid`.

| id | severity | category | title |
| --- | --- | --- | --- |
| M-01 | high | correctness | Current `session_resumed` schema and the domain frontier disagree in both directions |
| M-02 | high | correctness | Session 1.1/1.2 schemas drop text bounds and admit null refs the decoder rejects |
| M-03 | medium | correctness | Lineage counters: schema allows 2^63−1 strings; decoder also accepts JSON numbers and caps at 2^53−1 |
| M-04 | medium | correctness | Child-finding priority uses equality, so JSON `true` passes for priority 1 |
| M-05 | medium | correctness | JSON pointers: NFC and UTF-8 byte cap reject schema-valid pointers |
| M-06 | medium | performance | Every instance validation rebuilds a Draft 2020-12 validator and canonicalizes twice |
| M-07 | medium | quality | Non-validation failures inside schema checks become `schema_instance_invalid` |

## M-01

- **Severity:** high
- **Category:** correctness
- **Title:** Current `session_resumed` schema and the domain frontier disagree in both directions
- **File:lines:** `schemas/events/session-resumed-1.1.0.schema.json:1` (`$defs.Frontier`); `src/yoetz/domain/values.py:513-540` and `575-596`; `src/yoetz/domain/events.py:2863-2869` and `3690-3699`; `schemas/events/event-draft-1.2.0.schema.json:1` (oneOf branch refs `session-resumed-1.1.0`); `src/yoetz/domain/events.py:238` (`SESSION_EVENT_SCHEMA_VERSION = "1.1.0"`)
- **Evidence:** `session-resumed-1.0.0` points `resumed_frontier` at `common/frontier-1.0.0.schema.json`, whose `sequence` is a canonical decimal string and whose `head_digest` is `genesis` or `sha256:` plus 64 hex digits. `session-resumed-1.1.0` replaces that `$ref` with an inline object: `sequence` is `"type": "integer"` with no minimum/maximum, and `head_digest` is `"type": "string"` with no pattern and no length. `client_version` on 1.1.0 is likewise an unbounded string; 1.0.0 has `minLength` 1 and `maxLength` 256. The catalog advertises 1.1.0 as the live event version (`event_schema_versions` last-write for that name; locked by `tests/unit/protocol/test_models_and_schemas.py` and `tests/conformance/protocol/test_frozen_schemas.py`). `event-draft-1.2.0`, which `publish-work-request-1.2.0` references, includes this 1.1.0 payload branch. Domain `Frontier.as_wire` emits `sequence` via `render_wire_sequence` (a canonical string). `frontier_from_json` calls `parse_wire_sequence` on that field, and `parse_canonical_integer_string` rejects a non-string. `_decode_session_resumed` always uses `frontier_from_json`.
- **Failure scenario:** A payload that satisfies `session-resumed-1.1.0` (`"sequence": 1`, `"head_digest": "not-a-digest"`) is schema-valid and then fails in `frontier_from_json` (`noncanonical_integer_string`, or `invalid_frontier` once the type is forced). The bytes the service itself encodes (`as_wire`) fail the same schema because `sequence` is a string. A peer that trusts the checked-in 1.1.0 schema will emit integers and arbitrary digests; this tree will reject the integers and would accept a bad digest only after the integer check is aligned. `session_resumed` is not in the ordinary publish allowlist, so a caller draft dies at decode before the family check, but any re-validation of a stored 1.1.0 event against the catalog schema rejects the service's own canonical frontier.
- **Suggested fix and test:** Point `session-resumed-1.1.0` `resumed_frontier` back at `common/frontier-1.0.0.schema.json` (or copy its `sequence` pattern, digest `allOf`, and `additionalProperties: false`). Restore `client_version` to `minLength` 1, `maxLength` 256. Do not teach `frontier_from_json` to accept JSON numbers; ADR-002 and the shared frontier schema are one spelling. Test: `encode_payload` of a `SessionResumedPayload` at version 1.1.0 must pass `validate_schema_instance("session-resumed", "1.1.0", ...)`. The same call must reject `sequence` as a JSON number, `head_digest` of `"not-a-digest"`, and a missing digest pattern. Add the integer-sequence object as a negative golden vector under `fixtures/` so the 1.1.0 file cannot drift back to a pydantic `model_json_schema()` dump.
- **Confidence:** high
- **Related tests:** `tests/conformance/protocol/test_frozen_schemas.py` (version pin only), `tests/unit/protocol/test_models_and_schemas.py` (`event_versions["session_resumed"] == "1.1.0"`), `tests/unit/domain/test_event_payloads.py`, `tests/property/strategies/events.py` (domain frontiers, not the 1.1.0 schema)

## M-02

- **Severity:** high
- **Category:** correctness
- **Title:** Session 1.1/1.2 schemas drop text bounds and admit null refs the decoder rejects
- **File:lines:** `schemas/events/session-opened-1.0.0.schema.json:1`; `schemas/events/session-opened-1.1.0.schema.json:1`; `schemas/events/session-opened-1.2.0.schema.json:1`; `src/yoetz/domain/events.py:252-254`, `425-429`, `881-904`, `2432-2437`; `src/yoetz/domain/events.py:242` (`LINEAGE_SESSION_EVENT_SCHEMA_VERSION = "1.2.0"`)
- **Evidence:** `session-opened-1.0.0` bounds `task_title`, `external_ref`, and `workspace_ref` at 8192 and `client_version` at 256, all `minLength` 1, and the ref fields are strings, not null. `session-opened-1.1.0` and `session-opened-1.2.0` replace those with pydantic-shaped nodes: `client_version` and `task_title` are bare `"type": "string"`, and `external_ref` / `workspace_ref` are `anyOf` string or null with `"default": null`. Both 1.1.0 and 1.2.0 are branches of `event-draft-1.2.0`. Domain `_bounded_text` enforces `MAX_LABEL_BYTES` (256) on `client_version` and `MAX_TEXT_BYTES` (8192) on title and refs. `_optional` raises `invalid_event_value_type` when the key is present and the value is JSON null. Encode omits None via `_optional_value`, so the service never writes null, but the schema says null is valid.
- **Failure scenario:** A draft whose schema version is 1.2.0, with `"external_ref": null` or a `task_title` of 8193 characters, passes `publish-work-request` 1.2.0 schema validation and then fails in `decode_payload`. A 100k-character `client_version` is likewise schema-valid up to the 1 MiB request cap and is rejected only in `_bounded_text`. Callers and generated clients that follow 1.1/1.2 will send documents this decoder will not accept. The 1.0.0 branch still has the tight bounds, so the same logical field has two wire contracts depending on version.
- **Suggested fix and test:** Regenerate or hand-edit 1.1.0 and 1.2.0 so optional refs are omitted, not null (`do not` use pydantic `default: null` in the shipped schema), and copy the 1.0.0 `minLength`/`maxLength` onto `client_version`, `task_title`, `external_ref`, and `workspace_ref`. Keep the 1.2.0 lineage `allOf` (depth/origin/parent and project/generation pairs); that part matches `SessionOpenedPayload.__post_init__`. Test: a 1.2.0 instance with `"external_ref": null` must fail schema validation; an omitted ref must pass schema and `decode_payload`; a title of length 8193 must fail the schema; a title of length 8192 must pass both schema and `_bounded_text`.
- **Confidence:** high
- **Related tests:** `tests/unit/domain/test_event_payloads.py` (domain bounds), `tests/conformance/protocol/test_frozen_schemas.py` (version membership, not field bounds)

## M-03

- **Severity:** medium
- **Category:** correctness
- **Title:** Lineage counters: schema allows 2^63−1 strings; decoder also accepts JSON numbers and caps at 2^53−1
- **File:lines:** `src/yoetz/domain/events.py:260`, `575-609`, `920-924`, `1060-1067`, `2855-2858`; `src/yoetz/protocol/canonical.py:180-200`; `schemas/events/session-opened-1.2.0.schema.json:1` (`membership_generation` pattern); `schemas/events/child-dependencies-recorded-1.0.0.schema.json:1` (`lineage_authority_revision` pattern); `src/yoetz/protocol/consent.py:272-276`
- **Evidence:** The event schemas for `membership_generation` and `lineage_authority_revision` are strings matching the canonical pattern through `9223372036854775807` (2^63−1). `_lineage_revision` accepts either a Python `int` or a canonical decimal string, then `_bounded_integer(..., 1, _MAX_SAFE_INTEGER)` with `_MAX_SAFE_INTEGER = 9_007_199_254_740_991` (2^53−1). `_canonical_uint` does the same for coordination counters, including an extra `str(parsed) != value` check on the string path only. `consent.CoordinationBindingModel` documents the IEEE cap in a validator (`int(self.membership_generation) > 2**53 - 1`), but the event schema pattern was not narrowed to match. Because `decode_payload` is also used from ledger reads and importers without `validate_schema_instance`, a JSON number in these fields never hits the schema's `type: string` and is still accepted.
- **Failure scenario:** The string `"9007199254740992"` matches the 1.2.0 `membership_generation` pattern and is rejected by `_lineage_revision`. A publish or import that followed the schema is a false invalid. Separately, a stored or imported object with `"membership_generation": 1` (a JSON number) fails the event schema and still decodes, then `render_wire_sequence` rewrites it as `"1"`. Two byte forms of one counter exist on the read path; only the post-encode form is canonical. That is the integer-canonicalization gap the `_lineage_revision` comment says it closed, and it is closed only after a successful decode of a form the schema forbids.
- **Suggested fix and test:** Pick one range and one spelling. Prefer the canonical string already in the schema, and if the durable column is an IEEE-safe JSON number, narrow the schema pattern to `2^53-1` and reject `type is int` inside `_lineage_revision` / `_canonical_uint` (callers that already hold a domain int should pass it through a private constructor, not the wire decoder). Test: `decode_payload` of `session_opened` 1.2.0 with `membership_generation` `"9007199254740992"` must match the schema and the decoder (both accept or both reject). `membership_generation` as a JSON number must fail `decode_payload` with `invalid_event_value_type` even when schema validation is not run first. Round-trip `encode_payload(decode_payload(...))` must be byte-identical to the canonical string form.
- **Confidence:** high
- **Related tests:** `tests/unit/domain/test_event_payloads.py`; consent binding tests around `coordination_binding_generation_invalid` if present (the event schema is not covered by that cap)

## M-04

- **Severity:** medium
- **Category:** correctness
- **Title:** Child-finding priority uses equality, so JSON `true` passes for priority 1
- **File:lines:** `src/yoetz/domain/events.py:969-977` and `2604-2615`; `src/yoetz/domain/findings.py:723-725`; `tests/unit/domain/test_findings.py:276-279`
- **Evidence:** `Finding._validate_finding_fields` requires `type(priority) is not int` and equality with the kind's trait. `tests/unit/domain/test_findings.py` locks that with `priority=cast(int, True)` expecting `finding_priority_mismatch`. `ChildFindingSnapshot.__post_init__` only checks `self.priority != expected_priority`. In Python `True == 1` and `1.0 == 1`, so a priority-1 kind (`completion_with_open_obligations`, `claim_without_admissible_evidence`, and the other trait-1 kinds) accepts `True` and `1.0`. `_decode_child_finding` assigns `priority=cast(int, _field(...))` and does not check the type. The child-finding schema uses `"const": 1` with integer type, so a publish that goes through `event-draft-1.2.0` rejects JSON `true` (JSON Schema integers are not booleans). `decode_payload` on the ledger and importer paths does not call `validate_schema_instance`. Encode writes `value.priority` through unchanged, so an accepted `True` is re-serialized as JSON `true`.
- **Failure scenario:** A child-dependencies payload that reaches `decode_payload` with `"priority": true` and `"kind": "completion_with_open_obligations"` becomes a `ChildFindingSnapshot`, is treated as priority 1, and is written back as `true`. A later schema check of that encoding fails. Ranking uses `FINDING_KIND_TRAITS`, not the stored priority, so the bool does not reorder findings; it does let a non-integer through the type the schema and `Finding` both reject, and it can persist on any writer that builds the snapshot in process and skips the draft schema.
- **Suggested fix and test:** Use the same predicate as findings: `type(self.priority) is not int or self.priority != expected_priority`. Apply it in `_decode_child_finding` before the constructor so the cast cannot hide the type. Test: mirror `test_priority_and_origin_validation` for `ChildFindingSnapshot` with `True` and `1.0` on a priority-1 kind and a priority-2 kind (2 must still fail). Add a `decode_payload` case whose JSON contains `"priority": true` and expect `finding_priority_mismatch`.
- **Confidence:** high
- **Related tests:** `tests/unit/domain/test_findings.py` (`test_priority_and_origin_validation`); `tests/unit/kernel/test_lineage.py` (constructs `ChildFindingSnapshot` with real ints only)

## M-05

- **Severity:** medium
- **Category:** correctness
- **Title:** JSON pointers: NFC and UTF-8 byte cap reject schema-valid pointers
- **File:lines:** `src/yoetz/protocol/models.py:797-810` and `5169-5192`; `schemas/common/operation-result-1.0.0.schema.json:1` (`omitted_pointers` items: `maxLength` 256, pattern allowing non-ASCII); `src/yoetz/protocol/models.py:202` (`MAX_PROJECTION_POINTER_BYTES = 256`)
- **Evidence:** JSON Schema `maxLength` counts Unicode code points. The omitted-pointer schema allows 256 code points and a pointer pattern that permits non-ASCII segments. `_json_pointer_wire` and `_decode_pointer` reject the pointer unless `unicodedata.normalize("NFC", value) == value`, and they measure `len(value.encode("utf-8"))` against 256 bytes. ADR-002 decision 5 says the canonical profile does not Unicode-normalize; identity strings such as `workspace_ref` follow that and are not NFC-folded. Pointers are the exception, and they reject rather than normalize. A 200-character pointer of U+4E00 (3 bytes each) is 200 code points and 600 bytes: schema-valid, model-rejected. An NFD segment (`e` + U+0301) matches the pointer pattern and fails the NFC check. Confusable letters that are already NFC (Cyrillic `а` versus Latin `a`) are still distinct, which matches ADR-002; this finding is the NFC/byte mismatch, not a missing NFKC fold.
- **Failure scenario:** A privacy projection or result leaf whose `omitted_pointers` entry is schema-valid and either not NFC or longer than 256 UTF-8 bytes fails `JsonPointer` validation inside the pydantic model before the result schema is considered. A producer that followed the pointer schema is rejected. Two canonically equivalent pointer strings are not equal on this path, while the rest of the protocol treats canonically equivalent strings as different by design; the split is only on pointers.
- **Suggested fix and test:** Make the schema and the model the same check. Either lower the schema `maxLength` to a byte-oriented bound the model documents (and reject non-ASCII if the wire is ASCII-only), or change the model to enforce the schema's code-point `maxLength` and stop requiring NFC unless the schema pattern requires it. Do not silently NFC-normalize: that would make two input strings one digest. Test: a 200-character all-U+4E00 pointer must have one result from `validate_schema_instance` on the projection schema and from `_json_pointer_wire`. An NFD `/e\u0301` must likewise agree. An ASCII pointer of length 256 must pass both.
- **Confidence:** high
- **Related tests:** projection pointer tests in `tests/unit/protocol/` if they only use ASCII; none found that pair schema `maxLength` with the UTF-8 cap

## M-06

- **Severity:** medium
- **Category:** performance
- **Title:** Every instance validation rebuilds a Draft 2020-12 validator and canonicalizes twice
- **File:lines:** `src/yoetz/protocol/schemas.py:475-481`, `906-920`, `956-957`; `src/yoetz/protocol/canonical.py:144-147`, `275-310`; `src/yoetz/protocol/models.py:1406-1425` and `4066-4089`
- **Evidence:** `validate_schema_instance` calls `ensure_canonical_value`, which runs `_canonical_text` and builds the full canonical string only to discard it. It then copies the tree again in `_plain_validation_value` and constructs a new `Draft202012Validator` on every call. The catalog and registry are `@lru_cache`d; the validator is not. `_validate_model_against_schema` runs that during model validation, and `public_model_to_wire` runs it again on the same dump. `publish-work-request` 1.2.0 pulls in `event-draft-1.2.0`, whose `oneOf` has 40 payload branches (measured: 40 `$ref`s to event payload schemas). jsonschema evaluates `oneOf` by trying branches. Frame size is capped before parse on the control channel (`MAX_ORDINARY_CONTROL_FRAME_BYTES` 1_048_576, `MAX_CONTROL_FRAME_BYTES` 6_291_456) and hook stdin is capped before `strict_json_parse`, so this is not an unbounded allocation. `strict_json_parse`'s `_parse_int` still calls `int(literal)` before the 2^53 range check, so a digit string as long as the frame is fully materialized; that cost is bounded by the frame cap, not by a digit-length check.
- **Failure scenario:** Each public request pays two full canonical walks plus two compilations of the request schema, and a publish pays a 40-way `oneOf` per draft on each of those. A 1 MiB publish with 100 drafts is within `MAX_CANONICAL_REQUEST_BYTES` and still repeats that work. A hostile client that stays under the frame cap can spend the process on validator construction and `oneOf` backtracking rather than on ledger work.
- **Suggested fix and test:** Cache a frozen `Draft202012Validator` per `(schema_id)` on `_CatalogState` (the registry is already immutable). Change `ensure_canonical_value` to a non-allocating walk, or keep the canonical bytes when the caller needs them and skip the second call. Call `validate_schema_instance` once per model: either in the model validator or in `public_model_to_wire`, not both, unless the second call is a debug assert. Reject integer literals longer than 16 digits inside `_parse_int` before `int()`. Test: a unit test that `validate_schema_instance` on `publish-work-request` 1.2.0 uses one cached validator object across two calls (`validator is` identity or a counter on a factory). A benchmark is optional; the identity assertion locks the regression.
- **Confidence:** high
- **Related tests:** `tests/unit/protocol/test_models_and_schemas.py` (correctness of validation, not cost); catalog-load note in `schemas.py` around the digest-verified `check_schema` skip (issue #210) shows this cost was measured for meta-validation and not for instance validators

## M-07

- **Severity:** medium
- **Category:** quality
- **Title:** Non-validation failures inside schema checks become `schema_instance_invalid`
- **File:lines:** `src/yoetz/protocol/schemas.py:918-957`; `src/yoetz/protocol/errors.py:691-709` and `840-851`
- **Evidence:** After the `ValidationError` branch, `validate_schema_instance` has `except BaseException: raise SchemaInstanceInvalid() from None`. `BaseException` includes `KeyboardInterrupt`, `SystemExit`, and `MemoryError`, and also `RuntimeError` or `AttributeError` from a schema-compiler bug. The cause is cleared. `SchemaInstanceInvalid` always messages `schema_instance_invalid`. Separately, `ProtocolValueError` stores only a registered reason code, and `PublicOperationError._validate_message` bounds the message and strips controls; this audit did not find protocol or domain code that interpolates `str(exc)` into a public message. The defect here is a wrong code, not an internal leak. `normalize_safe_details` drops anything outside the allowlist, which is why a loose `safe_details` string in `public-error-1.0.0` (any string up to 4096) does not by itself escape through `PublicOperationError`.
- **Failure scenario:** A bug or `MemoryError` while validating a schema-valid body is reported to the caller as an invalid instance. The agent corrects a body that was fine, and the real fault is gone because `from None` drops the cause. A Ctrl-C during validation is turned into `SchemaInstanceInvalid` instead of aborting.
- **Suggested fix and test:** Catch `ValidationError` only. Let `ProtocolValueError`, `KeyboardInterrupt`, `SystemExit`, and `MemoryError` propagate. If a programming error must not reach the wire, catch `Exception`, log the correlation id, and raise a registered `internal_error` rather than `schema_instance_invalid`. Test: a stub validator that raises `RuntimeError` must not surface as `SchemaInstanceInvalid`; a stub that raises `KeyboardInterrupt` must propagate.
- **Confidence:** high
- **Related tests:** `tests/unit/protocol/test_errors.py` (reason-code registry); schema-instance projection tests that only cover `ValidationError`

## Checked, not filed

- **Extra fields.** `_ORDINARY_CONFIG` is `extra="forbid"`. Event payloads go through `_closed_object`, which raises `unknown_payload_field` for keys outside the required/optional sets (`src/yoetz/domain/events.py:2397-2418`). `PublicResultModel` uses `_ROOT_CONFIG` without `extra="forbid"` (`models.py:536-538`, `1137-1138`); the root value is a closed inner model, and no open root that keeps unknown keys was found.
- **Identifier collisions.** `PREFIX_BY_KIND` values are distinct 4-character prefixes. `validate_id` requires length 40, lowercase UUID text, version nibble `4`, and variant `8`/`9`/`a`/`b` (`src/yoetz/protocol/ids.py:60-95`, `125-163`). `new_id` draws 16 bytes from `os.urandom` and sets the version/variant bits. Actor ids are caller-asserted (`validate_actor_id`) and are not minted into the UUID namespace. No shared lookup that treats an actor id as another kind was found in these modules.
- **Unicode confusables on identity strings.** ADR-002 decision 5 says the profile does not Unicode-normalize. `workspace_ref` and `external_ref` are bounded strings with no NFC/NFKC fold (`models.py` `String1To8192`; `events.py` `_bounded_text`). Homoglyphs stay distinct commitments. That is the written decision, not a gap. Pointers are the inconsistent case (M-05).
- **Quarantine resurrection.** `StatusOperationPageModel._validate_operation_page` allows `continuation` only for `state="pending"` and `operation_kind="check"`, and it rejects `outcome`, frontiers, and `accepted_events` for every non-complete state, including `quarantined` (`models.py:3738-3794`). `lineage_operation_quarantined` maps to `lineage_terminal_review`, whose directive says not to reopen or clear durable state (`errors.py:590`; `recovery.py:302`). No protocol or non-observation domain path was found that turns a quarantined operation back into an accepted write.
- **Public error text leaking internals.** Reason codes are a closed registry. Safe-detail normalization allowlists keys and drops the rest (`errors.py:739-814`). No `str(exception)` interpolation was found in `protocol/errors.py`, `protocol/recovery.py`, or the domain modules in scope.


---

# CLI

Source notes: `14-cli.md`.

# CLI audit (read-only)

Scope: `src/yoetz/cli/` command surface listed in the assignment (`app.py`, setup, privacy setup, hooks and hook IO/diagnostics, bootstrap, entry, exits, menu, render, instance, isolation status, trusted console, startup gate and probe, startup context, agent start, closure, workspace binding, provider binding and status, recommend, project, Codex connection and subscription). Unlock, elevated, upgrade, observe, and host-integration wrappers were not re-audited.

Method: static reading of those modules and the unit/subprocess tests that lock their contracts. No source edits. No `yoetz` process against a real state directory. The checkout’s `requires-python` is `>=3.14,<3.15`. The VM interpreter is 3.14-incompatible 3.12, so unparenthesized `except A, B:` (PEP 758) was not treated as a defect. No timings were invented.

## Exec summary

Four defects. The one that hits every Codex prompt is that `hooks user-prompt-submit`, `hooks session-start`, and `hooks post-tool-use` still build the full Typer graph even though `entry.py` says a Codex hook never loads `cli.app`. The setup defect is that `yoetz setup run --non-interactive --accept` writes the first-run marker for a skipped or failed registration, and any marker file is treated as setup finished. Two smaller defects: activation/MCP consent text joins argv with spaces, and a rejected interactive `provider endpoint` selection exits 0.

Checked and not reported: vault and provider secrets are read from the trusted console or a bounded stdin buffer, not from flags. Hook observation fail-open (exit 0, empty JSON) is the documented host contract. The startup-gate shell allowlist rejects compound commands in tests. Wizard exit 0 with a JSON report is locked by `tests/subprocess/test_setup_wizard_cli.py` and is not, by itself, a lie.

---

## I-01

- Severity: medium
- Category: CLI startup import cost
- Title: Codex lifecycle hooks still import the full Typer application
- File: `src/yoetz/cli/entry.py:1-7`, `src/yoetz/cli/entry.py:296-371`; registered commands in `src/yoetz/adapters/integrations/codex_plugin.py:166-229`; handler imports in `src/yoetz/cli/hooks.py:14-49`; graph imports in `src/yoetz/cli/app.py:16-87`
- Evidence: `entry.main` fast-paths only `hooks observe`, `hooks cursor-observe`, `hooks claude-observe`, `hooks spool`, `hooks startup-context`, `hooks startup-gate`, and exact `service status`. The Codex plugin registers three other hook commands that miss those branches and fall through to `_run_full_cli()`:

  - `yoetz hooks session-start --workspace .` (timeout 15s)
  - `yoetz hooks user-prompt-submit --workspace .` (timeout 10s, every prompt)
  - `yoetz hooks post-tool-use`

  `_run_full_cli` imports `yoetz.cli.app`, which at module import constructs the Typer tree and loads typer, the workflow protocol models, `schema_document_for`, `yoetz.service.client`, and `yoetz.cli.project`. `hooks.py` then imports `render`, `connect_service`, and `StatusRequest` again. `handle_user_prompt_submit` additionally imports `observe_hooks` (`hooks.py:399-409`). The module docstring at `entry.py:1-7` says loading `cli.app` is a cost “a Codex hook never uses (#242)”.
- Failure scenario: Every Codex user prompt and every resume/compact starts a process that pays for the entire CLI before it reads stdin. That is the cost #242 removed from `hooks observe`, put back on the commands that still have to return `additionalContext` inside a 10s or 15s host timeout. A slow import leaves less of the budget for the status read in `handle_session_start` and for the observe call inside `handle_user_prompt_submit`.
- Suggested fix and test: Fast-path the three exact command shapes the same way as `hooks observe`: require the known flags, reject `--help`, missing values, and repeated options by returning `None`, and call `handle_user_prompt_submit`, `handle_session_start`, or `handle_post_tool_use` without importing `cli.app`. Keep unknown tokens on the Typer path so usage errors stay byte-identical. Add a unit test beside `tests/unit/cli/test_entry.py` that invokes each argv with `yoetz.cli.app` absent from `sys.modules` and asserts the handler ran. Do not claim the handlers are cheap after that: `hooks.py` still imports the service client; a follow-up can split the UserPromptSubmit cue from that import the way `hook_io.py` was split.
- Confidence: high
- Related tests: `tests/unit/cli/test_entry.py` (observe and `service status` only). No test asserts `cli.app` stays unloaded for `user-prompt-submit` or `session-start`.

## I-02

- Severity: medium
- Category: partial setup
- Title: Non-interactive `--accept` writes the first-run marker when registration was skipped or failed
- File: `src/yoetz/cli/setup.py:2802-2811`, `src/yoetz/cli/setup.py:423-430`, `src/yoetz/cli/setup.py:1299-1318`, `src/yoetz/cli/setup.py:1321-1328`; consumer `src/yoetz/cli/menu.py:199` and `src/yoetz/cli/setup.py:433-440`
- Evidence: Marker eligibility is:

```python
mutating_run = interactive or accept
setup_complete = (
    not interactive
    or (review_mode == "local_only" and privacy.get("outcome") in {"configured", "unchanged"})
    or (semantic_status is not None and semantic_status.get("semantic_ready") is True)
)
```

  `not interactive` makes `setup_complete` true before the registration outcome is consulted. With `--accept`, `mutating_run` is true, so `_write_setup_marker(str(registration["outcome"]))` runs for every outcome, including the early returns `"skipped"` / `foreign_entry_present` (`setup.py:1299-1318`) and `"failed"` / `skill_preview_failed` (`setup.py:1321-1328`). `_write_setup_marker` creates the file whenever the path is writable (`setup.py:549-560`). `setup_marker_present` is only `path.is_file()` (`setup.py:423-430`). It does not read `outcome`. `should_offer_first_run` then stays false, and the menu prints `complete` (`menu.py:199`).
- Failure scenario: `yoetz setup run --non-interactive --accept` against a Codex home that already has a foreign MCP entry returns exit 0 and a report whose registration outcome is `skipped` (locked by `test_foreign_entry_is_preserved_and_reported`). The same run writes `yoetz.setup-wizard-marker/1` with `"outcome": "skipped"`. The next interactive `yoetz` does not offer first-run setup, and the menu says setup is complete, while the report’s own `next_step` says to rerun setup with a new `--codex-home`. A failed skill preview does the same with `"outcome": "failed"`.
- Suggested fix and test: Write the marker only when the registration outcome is one of `registered`, `reregistered`, or `already_registered`. Keep the interactive gates that also require local-only privacy configured or `semantic_ready`. Extend `test_foreign_entry_is_preserved_and_reported` to assert `marker_written` is false and the marker path is absent. Add a failed-preview case that asserts the same. Assert `setup_marker_present()` is false afterward so the menu cannot report `complete`.
- Confidence: high
- Related tests: `tests/subprocess/test_setup_wizard_cli.py:486-493` (dry run, no marker), `569-601` (successful `--accept` writes the marker even when the service is unreachable), `645-658` (foreign entry, exit 0, marker not asserted).

## I-03

- Severity: medium
- Category: help text that does not round-trip as a shell command
- Title: Setup consent echoes activation and refreshed MCP argv joined by spaces
- File: `src/yoetz/cli/setup.py:816-826`, `src/yoetz/cli/setup.py:1636-1644`; correct sibling at `src/yoetz/cli/setup.py:759-760` and `src/yoetz/cli/setup.py:850`. Same pattern in `src/yoetz/cli/render.py:275-279` and `src/yoetz/cli/render.py:615-621`
- Evidence: The registration preview prints the MCP serve command with `shlex.join(serve_command)` (`setup.py:760`). The same function then prints the commands the operator is confirming as:

```python
typer.echo(f"    {activation_preview.executable_path} {' '.join(command)}")
```

  for `probe_command`, `inventory_command`, and `install_command` (`setup.py:821-826`). After plugin activation changes the MCP entry, the updated preview uses `' '.join(refreshed.serve_command)` (`setup.py:1641`) and then `typer.confirm` (`setup.py:1644`). The bytes that are actually applied stay the structured argv; only the text the human confirms is unquoted. `render_human_status` and `render_human_awaiting_human` label a continuation `"Trusted command: "` plus `' '.join(...)`.
- Failure scenario: Codex or the Yoetz launcher lives under a path that contains a space or a shell metacharacter (`/home/a b/codex`, or a home directory with a quote). The consent screen shows a line that a shell would split into a different executable and arguments than the argv Yoetz will run. An operator who copies the “exact activation command”, or who scans it to see which binary is about to be enabled, is not looking at the command that will run. `shlex.join` on the MCP line in the same preview shows the safer rendering was already chosen for one of the commands.
- Suggested fix and test: Render every displayed argv with `shlex.join`, including the executable as the first element (`shlex.join((os.fspath(executable), *command))`). Do that in `_emit_registration_preview`, the stale-preview confirm, and the two human renderers. Test with an executable path containing a space and a quote and assert the echoed line round-trips through `shlex.split` to the original tuple. The apply path should stay on the structured argv; this change is display-only.
- Confidence: high
- Related tests: setup wizard tests assert digest and outcome text, not shell-quoting of the activation lines. `src/yoetz/cli/setup_readiness.py` and `src/yoetz/cli/recommend.py:343` already use `shlex.join`.

## I-04

- Severity: low
- Category: exit-code lie
- Title: A rejected interactive provider-endpoint choice exits 0
- File: `src/yoetz/cli/provider_binding.py:177-183`, `src/yoetz/cli/provider_binding.py:202-218`; `src/yoetz/cli/app.py:3961-3980`
- Evidence: `prompt_provider_endpoint_binding` returns `None` for skip (`s`), for an input outside `1`–`9`, and for `ConfigError` on write. The invalid and config-error branches print `invalid_request: ...` to stderr and still return `None`. `provider_endpoint` on a TTY with no selector calls that function and then `_finish(0)` for every return except the string `codex_subscription`, which also exits 0 after a next-step line. `_finish` raises only for a non-zero code (`app.py:767-769`). `tests/subprocess/test_provider_endpoint_cli.py:117-133` locks “prompt returned None → exit 0”, which is correct for skip and wrong for the error returns that share it.
- Failure scenario: On a TTY, `yoetz provider endpoint` and a non-numeric answer prints `invalid_request` and exits 0. A wrapper that treats exit 0 as “a provider binding was written or explicitly skipped” cannot tell a typo from skip. A `ConfigError` during the write (invalid origin, bad model) does the same: stderr says `invalid_request`, the process succeeds, and no binding was written.
- Suggested fix and test: Return a small result (written path, codex-subscription sentinel, skip, or failure) instead of overloading `None`. Exit 0 on skip and on a path that was written. Exit 2 when the picker already printed `invalid_request`. Keep `test_bare_interactive_endpoint_enters_generic_picker` for an explicit skip, and add a CliRunner case whose prompt input is `nope` and whose exit code is 2.
- Confidence: high
- Related tests: `tests/subprocess/test_provider_endpoint_cli.py:117-141`.

## Checked, no defect filed

- Secret flags: provider credentials and vault passphrases are not command-line options. `app.py:4719-4748` reads one stdin secret into a `bytearray` and wipes it. `trusted_console.py` refuses a non-foreground tty and does not fall back to redirected stdin.
- Startup gate: `bootstrap_tool` denies `;`, `|`, `$()`, and a foreign launcher. `tests/unit/cli/test_startup_gate.py:667-687` locks that. Extra arguments after an allowlisted `yoetz service restart` are ignored by the gate; the runbook says those commands only reach ordinary host admission.
- `run_setup_wizard` returning 0 with a failure inside the JSON report is the locked wizard contract (`test_foreign_entry_is_preserved_and_reported`, credential-failed interactive case at `test_setup_wizard_cli.py:1073`). I-02 is the marker side effect, not that exit code.
- `resolve_session_workspace` falling back from a missing host `cwd` to the hook cwd is locked by `tests/unit/cli/test_hooks.py:1467-1471`. An explicit path that fails canonicalization does not fall through (`1473-1477`).
- Isolation digests and instance root checks go through `verify_private_local_bundle` after resolve. No path-escape bug was shown in `instance.py` or `isolation_status.py`.


---

# MCP server

Source notes: `15-mcp.md`.

# MCP audit (15)

Read-only review of `src/yoetz/mcp/`, the application modules that register or record an MCP route (`applied_mcp_route.py`, `harness_mcp.py`, `host_connection.py`, `integrations.py`, `codex_plugin.py`), and `tests/unit/mcp`. No source was modified.

Pinned host SDK: `mcp==1.28.1` (`pyproject.toml`). Cancellation behavior below was checked against that wheel (`mcp/shared/session.py`, `mcp/server/lowlevel/server.py`) and a local anyio 4.9 probe. It was not inferred from the unit test that uses `asyncio.Task.cancel()`.

## Executive summary

The tool and resource surface is closed and fail-closed: seven registered names, guidance reads only by exact registry URI, validation errors projected to allowlisted pointers, no shell or SQL built from tool arguments, and presentation schemas cached at import. Session and writer authorization for ledger tools is enforced in the service after the bridge forwards the validated request, not inside the MCP module.

One confirmed defect: a host `notifications/cancelled` does not cancel an in-flight non-check service call. The MCP SDK cancels the handler with an anyio cancel scope; `ServiceClient.call` then awaits the control-cancel send inside that scope, and the send is aborted. `asyncio.Task.cancel()` still delivers the frame, which is what the unit test covers. `check` skipping cancel is intentional and documented.

## Findings

### T-01

- **Severity:** high
- **Category:** correctness
- **Title:** Host MCP cancellation does not cancel the in-flight service call
- **File:lines:** `src/yoetz/mcp/server.py:2535-2560` (dispatch has no cancel path); `src/yoetz/service/client.py:813-835` and `src/yoetz/service/client.py:891-899` (cancel send aborted). SDK: `mcp/shared/session.py` `RequestResponder.cancel` (scope cancel) and the `CancelledNotification` branch; `mcp/server/lowlevel/server.py` runs the handler in `with responder:`.
- **Evidence:**
  - `call_tool` / `_handle_call_tool_request` only validate the name, optionally bind a Cursor workspace, and await the operation. There is no `CancelledNotification` handler in `src/yoetz`.
  - MCP 1.28.1 enters `RequestResponder` as a context manager, which opens an `anyio.CancelScope`. `notifications/cancelled` calls `RequestResponder.cancel()`, which calls `CancelScope.cancel()` and then writes a JSON-RPC error `"Request cancelled"`. The tool handler, including `ServiceClient.call`, runs inside that scope (`server.py` `with responder:` around `_handle_request`).
  - On `asyncio.CancelledError` after the call frame was sent, `ServiceClient.call` awaits `_request_cancel` for every method except `ControlMethod.CHECK`. `_request_cancel` does `await asyncio.wait((task,), timeout=...)` on the send task.
  - Reproduced with anyio 4.9: a task inside `anyio.CancelScope` that catches `CancelledError` and then `await asyncio.wait` on a send task gets `CancelledError` at that wait. The handler cancels the send task and returns. Observed trace: `['aborted-send', 'outer-cancelled']`. The cancel payload was never written.
  - The same pattern under `asyncio.Task.cancel()` on Python 3.12 completes the inner wait. That is why `tests/unit/service/test_client.py` `test_task_cancellation_sends_one_way_distinct_cancel_frame` can pass without covering the MCP scope.
  - Timeout cancellation is a different path: `asyncio.timeout` raises `TimeoutError`, which is not the responder scope, and `_request_cancel` is awaited on a live task (`client.py:880-884`). That path is not this bug.
  - Skipping cancel for `ControlMethod.CHECK` is specified, not accidental. `docs/INTERFACES.md` (“Long semantic check waits”) and `docs/adr/ADR-006-semantic-provider-profile.md` say a client wait timeout or local coroutine cancellation leaves an admitted semantic check running; an explicit attached control cancel or service shutdown cancels it. `test_cancelled_check_wait_consumes_late_result_without_cancelling_review` locks the check exception.
- **Failure scenario:** The host sends `tools/call` for `start`, `publish_work`, `respond`, `status`, or `receipt`, then `notifications/cancelled` (user stop, or the client dropping the request). The SDK tells the host the request was cancelled and stops the bridge handler. The Unix-socket call stays in flight until its deadline (`_WORKFLOW_RPC_DEADLINE_MS` is 30s in `server.py`). A `publish_work` or `start` that the user stopped can still commit. The bridge never sends `ControlCancelRequest`, so the service does not take the explicit-cancel path that `client.call` attempts for those methods.
- **Suggested fix and test:**
  - Shield only the cancel send from the caller’s cancel scope, then re-raise `CancelledError`. The shield has to cover `_send`, not merely `asyncio.wait`, because the scope cancels the wait and today’s `except` cancels the send task. One shape: start the send, then `with anyio.CancelScope(shield=True): await asyncio.wait(...)`, and on shield timeout cancel the send as today. Do not shield the original RPC wait, and do not send cancel for `ControlMethod.CHECK`.
  - Add a unit test that runs `client.start` or `client.service_status` inside `with anyio.CancelScope() as scope`, waits until the call frame is queued, calls `scope.cancel()`, and asserts a second frame with `kind=cancel` and `target_rpc_id` equal to the call `rpc_id`. Keep `test_task_cancellation_sends_one_way_distinct_cancel_frame` so both cancel mechanisms stay covered. Keep the check test asserting no cancel frame.
  - Optional bridge test: stdio client sends `tools/call` `publish_work`, then `notifications/cancelled`, and the fake service observes a `cancel` frame before the deadline. That test belongs next to `tests/subprocess/test_mcp_service_bridge.py` or `tests/unit/service/test_client.py`, not only `tests/unit/mcp`, because the abort is in the client the bridge uses.
- **Confidence:** high
- **Related tests:** `tests/unit/service/test_client.py` (`test_task_cancellation_sends_one_way_distinct_cancel_frame`, `test_cancelled_check_wait_consumes_late_result_without_cancelling_review`); `tests/unit/mcp/test_availability_latch.py` (`test_cancelled_first_attempt_releases_waiters`, Task.cancel during connect, which does not model the responder scope); `tests/integration/service/test_daemon_clients.py` (daemon `CancelledError` maps to `request_cancelled` only when the service task itself is cancelled).

## Checked, not filed

These were hunted and did not produce a defect beyond T-01.

- **Schema versus implementation.** `ToolDescriptor.input_schema` / `output_schema` are `functools.cache`d (`descriptors.py` `_mcp_presentation_schema`, `_mcp_output_presentation_schema`). Import runs `_lint_descriptor_sets` and eagerly touches every schema. Advertised `start` / `respond` / `status` schemas drop root `allOf` (`descriptors.py:1457-1458`); frontier `allOf` is flattened to a pattern plus description. Catalog admission still runs through the Pydantic models in `_dispatch`. The drop is locked by `tests/conformance/surfaces/test_mcp_contract_matrix.py` and `tests/conformance/surfaces/test_agent_surface_authorability.py`. Output `prefixItems` are rewritten so Cursor’s legacy `items` validator does not reject valid tuples (`_legacy_compatible_output_arrays`). Weaker host schemas plus stricter admission is the recorded dual surface, not a second validator.
- **Tool authz.** Ledger tools require a service session. `application/service.py` rejects a `writer_id` that does not match `session_binding`. The bridge does not add a second principal; the local MCP peer is the user’s service client (`ControlClientKind.MCP_BRIDGE`). `read_guidance` and `resources/read` serve five static URIs and do not take a session. Cursor ledger calls go through `_ensure_cursor_workspace_binding` before `call_tool`; `read_guidance` is the documented exception (`server.py:2578-2586`).
- **Resource escape.** `resources.read_resource` accepts only an exact key of `_RESOURCE_BY_URI`. `logical_name` values are literals. Bytes come from `read_verified_resource`, which requires a manifest entry and a matching size and digest (`version.py`). URI text is not a path. `server.read_resource` maps `GuidanceResourceError` to `ValueError("guidance_resource_unavailable")`.
- **Error text.** Unknown tool names are discarded (`errors.sanitize_unknown_tool_name`). Pydantic failures become allowlisted pointers and closed reason tokens (`safe_validation_locations`; `include_input=False`). `condition_value` is a schema const, bounded to 64 ASCII characters (`protocol/schemas.py` `SchemaInstanceInvalid`). Public `safe_details` pass `normalize_safe_details` (`head_digest` must match the digest pattern). Control errors use fixed sentences. Cursor workspace failures do not echo the path.
- **Output bounds.** Non-Cursor text is `render_safe_compact_summary` at 512 bytes. Cursor text is canonical JSON of the same wire the structured result already carries (`server.py` `_result_text`); `tests/subprocess/test_mcp_service_bridge.py` locks that. Stdio frames cap at `MAX_JSON_FRAME_BYTES` (1 MiB). `read_guidance` returns the document as tool text, with `ReadGuidanceSuccessModel` capping text and `byte_count` at 65,536; current guidance files are smaller (largest `guidance/request-templates.md`, 52,733 bytes).
- **Shell and SQL.** No `subprocess`, `shell=True`, or SQL execution in `src/yoetz/mcp`. Cursor roots are decoded and passed through `canonical_workspace_locator`, not a shell. Applied-route storage parses JSON with duplicate-key rejection and writes only validated command tokens (`applied_mcp_route.py`).
- **Check cancellation.** Leaving an admitted semantic check running after local wait cancellation matches `docs/INTERFACES.md` and ADR-006. Not the same bug as T-01.
- **Descriptor generation cost.** Presentation schemas are cached. `list_tools` reuses the cache. Import cost is the fail-closed lint, not a per-call rebuild.
- **Application route modules.** `harness_mcp.py` and `codex_plugin.py` bind registration and plugin install to a confirmed preview digest. `host_connection.py` errors are reason tokens (`ConnectionError`). They do not implement `tools/call`.


---

# Terminal UI

Source notes: `16-tui.md`.

# TUI audit (`src/yoetz/tui/`, `tests/unit/tui`, `tests/tui`)

Read-only. No source was modified. No live Yoetz ledger was available in this session.

Scope read: `app.py`, `runtime.py`, `render.py`, `models.py`, `commands.py`, `events.py`, `text.py`, `symbols.py`, `styles.py`, `widgets/*`, and the unit/integration TUI tests. Protocol and service code was opened only to check what the TUI claims against the values it actually receives.

Not found, after looking:

- No runaway refresh loop. `SelectionView.on_resize` and `SessionHeader.on_resize` redraw, but the compact/header predicate uses screen height, and option text is a pure function of width, so a second pass is stable.
- No secret widget path. `TextEntryView` accepts `password=True`, but no caller passes it. Provider keys and passphrases go through `hand_over_terminal` / `app.suspend()`. `tests/tui/test_input_safety.py` locks the credential step as an approval, not an input.
- Control-channel calls (`ServiceClient.check` / `status` / `receipt`) await `write_control_frame`. They do not `recv` on the UI thread. The UI-thread block that does exist is synchronous process and filesystem work (U-09), not the Unix socket.

---

## U-01

- **Severity:** high
- **Category:** honesty (receipt / findings / evidence)
- **Title:** Opening a task paints obligations as evidence, hides findings, and always says no receipt and no check
- **File:** `src/yoetz/tui/runtime.py:1602-1620`, rendered by `src/yoetz/tui/render.py:618-652`, promised by `src/yoetz/tui/commands.py:36`

**Evidence.** `_work_detail` is the only builder `open_task` returns. It ignores `claims`, `checks`, and `findings` (they stay the `WorkDetail` defaults of `()`). It stores `open_obligation_count` in `evidence_count`, hardcodes `last_check="not run in this session"`, and hardcodes `receipt_available=False`. `receipt_blocking_finding_count` on `StartCompactViewModel` is never read.

`render_work_detail` then prints:

- `Evidence` as that obligation count, or `unknown` when the count is `None`
- `Receipt` as `not available yet` whenever `receipt_available` is false
- `Claims` / `Checks` / `Findings` as `none recorded` when the tuples are empty
- `Open findings` from `unanswered_finding_count`

`tests/unit/tui/test_runtime.py` (`test_work_detail_preserves_unknown_open_obligation_count`) locks the obligation-to-`evidence_count` mapping. `tests/unit/tui/snapshots/work_detail.txt` shows what a fully populated `WorkDetail` can say (`Evidence 3`, `Receipt available`, real findings). The runtime never builds that object. `/work`'s own summary says it opens a task "to view claims, evidence, and findings".

**Failure scenario.** A task whose compact view is `unanswered_finding_count="3"`, `open_obligation_count="0"`, with recorded claims and a buildable receipt, is shown as:

- `Evidence 0` (zero open obligations, read as no evidence)
- `Open findings 3` and, under `Findings`, `none recorded`
- `Last check: not run in this session` even after `/check` in this session, because a later `/work` calls `open_task` again and the string is constant
- `Receipt: not available yet`, so the user can believe `/receipt` has nothing to show

Empty `known_gaps` is also rewritten to the single coverage line `no gaps recorded` (`runtime.py:1617`), including when `coverage` itself was missing (`getattr` default). That is a positive coverage sentence, not "could not be read".

**Suggested fix and test.** Map fields to their names: evidence stays unknown unless a real evidence count exists; show `open_obligation_count` as open obligations; copy finding summaries or say `not loaded` rather than `none recorded` when the compact view did not include them; set `receipt_available` from whether a receipt id/document is actually present, or drop the row; set `last_check` from the last check this runtime recorded (verdict + mode) or `unknown`. Add a runtime test whose compact object has `unanswered_finding_count="2"`, `open_obligation_count="0"`, and non-empty gaps, and assert the rendered lines do not contain `Evidence             0`, `Findings\n  none recorded`, or `not available yet`.

**Confidence:** high

**Related tests:** `tests/unit/tui/test_runtime.py::test_work_detail_preserves_unknown_open_obligation_count` (locks the mis-mapping, does not render it); `tests/unit/tui/test_render.py::test_work_detail_shows_every_layer_a_receipt_would_report` (renderer only, fed by `tests/builders/tui.py::work_detail`).

---

## U-02

- **Severity:** high
- **Category:** honesty (service / review status)
- **Title:** `/status` says the local service could not be read for open work, on every snapshot, including a healthy service
- **File:** `src/yoetz/tui/runtime.py:1406-1415`, `src/yoetz/tui/render.py:514-519`

**Evidence.** After `status_snapshot` has already classified `service_reachable` from `vault_posture()`, it always sets `open_work, open_findings, readable = 0, 0, False`. The comment says this is because the control protocol has no task list, and that these zeros are not a claim that the list is empty. `render_status` only avoids the zero-task claim. The sentence it uses is `unavailable — the local service could not be read`.

The same screen's service row comes from `service_reachable` and, when the vault state is `ready`, `_status_word` prints `ready` (`render.py:539-540`).

**Failure scenario.** Service is up and `/status` shows `Local service … ready` and, a few rows later, `Open work … unavailable — the local service could not be read`. The user is told the service failed a read that succeeded. The comment and the sentence disagree.

**Suggested fix and test.** Pass a distinct reason (`no_task_list` vs `service_unreachable`) into `StatusSnapshot`, and render `open work is not listed by this protocol` when the service row is ready. When `vault_posture` actually failed, keep the current sentence. Test `render_status` with `work_readable=False` and a `service_reachable=VERIFIED` layer, and assert the work row does not say the service could not be read.

**Confidence:** high

**Related tests:** `tests/unit/tui/test_render.py::test_status_reports_unreadable_work_as_unavailable_not_as_zero` only asserts the substring `unavailable` and the absence of `0 tasks`. It does not build a snapshot the way `status_snapshot` does.

---

## U-03

- **Severity:** high
- **Category:** honesty (external review)
- **Title:** "External review: verified" is structural `semantic_ready`, and the live-probe layer is never filled
- **File:** `src/yoetz/tui/runtime.py:1132-1150`, `src/yoetz/tui/runtime.py:1478-1506`, `src/yoetz/tui/render.py:507-508`, `src/yoetz/tui/render.py:532-541`

**Evidence.** `provider_posture` copies `semantic_ready` from `provider_status_report` and never sets `ProviderPosture.transport_tested` (default `False` in `models.py:386`). `_provider_layers` then marks `semantic_review_ready` `VERIFIED` when `semantic_ready` is true, with an empty detail, and marks `provider_transport_tested` `UNPROVEN` with `no live probe has run` on every call.

`/status` shows only `semantic_review_ready`, labeled `External review`. `_status_word` turns `VERIFIED` into the word `verified`. The transport layer is omitted from that summary (it appears only in the `D` layer dump via `render_layers`).

`provider_status_report` documents the flag it is copying (`src/yoetz/cli/provider_status.py:869-876` and the note at `917-918`): `semantic_ready` is structural (service ready, endpoint, credential, inference channel, repository grant). It does not prove live dispatch. `symbols.py:46-47` defines `VERIFIED` as "a postcondition was actually observed".

**Failure scenario.** Credential stored, privacy channel on, grant present, no provider round-trip ever attempted. `/status` prints `External review … verified` with a green tick. The detail dump, if opened, simultaneously says `Provider connection tested … not proven — no live probe has run`. A stored binding is presented as a verified review path. `render_finish` is slightly weaker (`ready`, because the key does not end in `verified`) but `/status` is not.

**Suggested fix and test.** Drive `semantic_review_ready` with the same predicate the service uses for "dispatch proven", or label the row `structurally ready` / `not proven` and keep `verified` for a recorded live probe. Plumb a real probe result into `transport_tested` or stop rendering a probe line that cannot become true. Test `status_snapshot` (or a posture fixture through `_provider_layers` plus `render_status`) with `semantic_ready=True` and no probe, and assert the status row does not contain `verified`.

**Confidence:** high

**Related tests:** `tests/unit/tui/test_render.py::test_stored_configuration_is_never_reported_as_a_working_provider` covers `render_provider_stored` only. Nothing asserts the `/status` external-review word against `semantic_ready`.

---

## U-04

- **Severity:** medium
- **Category:** honesty (local checks)
- **Title:** "Local checks" is marked verified when the vault is merely ready
- **File:** `src/yoetz/tui/runtime.py:1393-1400`

**Evidence.** The `local_checks` layer is `LayerState.VERIFIED` iff `vault.ready`, with an empty detail. No check is run. `render_layers` / the `D` details on `/status` print the state word `verified` and the verified glyph (`render.py:488-495`). `/doctor` prints that state as `ok` (`render.py:687-693`). The summary row softens `VERIFIED` to `ready` (`render.py:539-540`), which is still a capability claim inferred from vault state.

**Failure scenario.** Vault unlocks, the check sandbox is absent, and an approved check would be rejected. `/status` details still show `Local checks … verified`. The sandbox fact exists only on the doctor host entries (`runtime.py:254-269`), not on this layer.

**Suggested fix and test.** Leave `local_checks` at `UNPROVEN` until a check has completed in this process, or rename the layer to "service can accept a check" and do not use `LayerState.VERIFIED`. Test `status_snapshot` with a fake ready vault and assert the layer state is not `VERIFIED`.

**Confidence:** high

**Related tests:** none on this inference. `tests/builders/tui.py:120` hardcodes the layer as verified.

---

## U-05

- **Severity:** medium
- **Category:** honesty (setup completion)
- **Title:** Setup completion always opens with a green "Yoetz is ready"
- **File:** `src/yoetz/tui/render.py:399-402`, `src/yoetz/tui/app.py:969-975`

**Evidence.** `render_finish` always starts with `_bullet(Level.VERIFIED, "Yoetz is ready")`. `_finish_setup` always `say`s that block at `Level.VERIFIED` after `write_setup_marker`, for every path that reaches it: local-only setup (`app.py:545-546`), semantic setup (`564`), "finish as local only" after an incomplete provider (`619`), and the foreign-entry "local only" choice (`840`). The rows underneath can still be `blocked`, `not configured`, or `not proven` via `_finish_word`.

The snapshot `tests/unit/tui/snapshots/finish.txt` is the healthy builder fixture (`service_reachable` verified). It does not constrain an unhealthy snapshot.

**Failure scenario.** User skips storage because the service is not running (`_choose_storage` returns after "The local service is not running yet", `app.py:869-877`), then finishes local-only. The transcript shows `✓ Yoetz is ready` above `Local service … blocked` and `Secure storage … unknown`.

**Suggested fix and test.** Choose the heading from the layers: verified tick only when service, vault, and the chosen review path are in the states just promised; otherwise `Yoetz is set up, with steps still open` at `UNPROVEN` or `BLOCKED`. Test `render_finish` with `service_reachable=BLOCKED` and assert the first line does not use the verified glyph or the word `ready`.

**Confidence:** high

**Related tests:** `tests/unit/tui/test_render.py::test_finish_reports_off_layers_as_off_and_never_as_verified` (external-review row only).

---

## U-06

- **Severity:** high
- **Category:** honesty (check verdict and cancellation)
- **Title:** Every check verdict is painted unproven, and Ctrl+C says nothing changed while the check keeps running
- **File:** `src/yoetz/tui/app.py:311-323`, `src/yoetz/tui/app.py:2054-2058`; service behavior consumed here: `src/yoetz/service/client.py:891-895`

**Evidence.** `CheckSuccessModel.verdict` is `action_required | incomplete_check | insufficient_coverage | no_issue_detected` (`src/yoetz/protocol/models.py:2639-2643`). There is no `pass`. The TUI does:

```python
level = Level.VERIFIED if verdict == "pass" else Level.UNPROVEN
self.settle(level, f"Check complete: {verdict}", lines)
```

The verified branch is dead. `action_required` and `no_issue_detected` get the same `!` / warning glyph (`symbols.py:59-65`). `symbols.py` says that glyph means "configured, limited, stale, or simply never demonstrated", and `BLOCKED` is "failed, refused, or stopped".

Ctrl+C with no temporary view calls `flow.cancel()` and then `say(Level.UNPROVEN, "Stopped. Nothing further was changed.")`. `command_check` awaits `run_check` in a separate task and, in `finally`, `check.cancel()` if that task is not done (`app.py:2050-2052`). `ServiceClient.call` on `CancelledError`, after the frame was sent, requests cancel for every method except `ControlMethod.CHECK` (`client.py:891-895`). The TUI's own comment at `app.py:2043-2044` says the check keeps running service-side. The interrupt sentence denies that.

`awaiting_human` is settled at `Level.ACTIVE` (`app.py:2054-2056`), the glyph for "work is running right now", not for a decision waiting on the user.

**Failure scenario.**

1. A check returns `action_required` with findings. The title is yellow `! Check complete: action_required`, same certainty as `no_issue_detected`. The body text from `render_human_check` is accurate; the certainty glyph is not.
2. User hits Ctrl+C after `/check` has sent the request. The transcript says nothing further changed. The service still records the check. A later `/receipt` can include it. If the client task had already finished and the UI was inside `check_progress`, cancellation still skips `check.result()`, so the verdict line is never settled and the `• Checking …` line stays.

**Suggested fix and test.** Map `no_issue_detected` to a non-failure presentation only if the rendered body is shown unchanged; map `action_required` to `BLOCKED`; map `insufficient_coverage` and `incomplete_check` to `UNPROVEN`. Do not compare to `pass`. On interrupt, if a check request id is in `_check_request_ids` or the check task has started, say the check may still finish on the service and point at `/progress`. Do not say nothing changed. Test with a fake `run_check` result of `action_required` and assert the settled event level is `BLOCKED`. Test Ctrl+C during a hung `run_check` and assert the transcript does not contain `Nothing further was changed.`

**Confidence:** high

**Related tests:** `tests/tui/test_input_safety.py::test_ctrl_c_with_no_view_open_stops_the_active_flow_safely` only checks that the process did not die at idle. `tests/unit/tui/test_workflow_contract.py` checks the request shape, not the glyph.

---

## U-07

- **Severity:** high
- **Category:** crash / exception handling (stuck transcript, dropped check)
- **Title:** A connected-client `ControlError` escapes the command worker, and any non-`RuntimeError_` during the progress poll abandons the check result
- **File:** `src/yoetz/tui/runtime.py:790-804`, `src/yoetz/tui/app.py:376-381`, `src/yoetz/tui/app.py:2037-2058`

**Evidence.** `_client` turns `ControlError` into `RuntimeError_` only when `build_service_client` fails. `client.start` / `check` / `status` / `receipt` are awaited inside the `async with` and are not wrapped. `ServiceClient.call` raises `ControlError` for `request_timeout`, `service_unavailable`, and `frame_invalid` (`client.py:890`, and the `_invoke` path).

`_dispatch` catches `CancelledError` and `RuntimeError_` only. Anything else kills that worker. `command_check` posts `• Checking …` with `say` before the wait. `settle` runs only after `check.result()` returns normally. An escaping `ControlError` leaves that activity line in place. `_report` is never called.

The progress loop is stricter. `check_progress` failures that are `RuntimeError_` are swallowed (`app.py:2047-2048`) and the poll continues. Any other exception (including `ControlError` from `client.status`, or `TypeError` from `render_human_status` if the unwrapped page is not exactly `StatusSuccessModel`) hits `finally` and `check.cancel()` if the check task is not done, then propagates. `check.result()` is never read, so a check that already completed during the status call is also dropped. `render_human_status` raises `TypeError("status_result_invalid")` unless `type(result) is StatusSuccessModel` (`src/yoetz/cli/render.py:261-262`).

**Failure scenario.** `/check` is in flight, the socket times out on the next progress `status`. The UI stops polling, cancels the client waiter (the service check is still not cancelled; see U-06), the worker dies with an uncaught `ControlError`, and the transcript still shows `Checking …` with no reason line. `/work` hitting `ControlError` after connect has the same uncaught path: `Opening …` stays, and `_report` does not run.

**Suggested fix and test.** Wrap the body of `_client` so `ControlError` from the yielded calls becomes `RuntimeError_`, or catch `Exception` in `_dispatch` and `_report` it after settling the in-flight line to `BLOCKED`. In `command_check`, catch progress errors without cancelling a check that is still running; on the way out, `settle` the heading to the error. If the check task is done, always `result()` it before propagating. Test a fake client whose `status` raises `ControlError("request_timeout")` during the poll and whose `check` returns `no_issue_detected`, with `progress_poll_seconds=0`, and assert the transcript contains the verdict or a bounded reason, not a leftover `Checking` title alone.

**Confidence:** high

**Related tests:** `tests/unit/tui/test_workflow_contract.py` (happy-path request bodies; the fake client returns `ok=False`, which `_unwrap` does convert). No test injects `ControlError` from `client.check` or `client.status`.

---

## U-08

- **Severity:** medium
- **Category:** honesty (consent and privacy recommendation) / exception swallowing
- **Title:** A failed consent read is shown as "not configured", and a failed privacy recommendation is explained as if configuration were read
- **File:** `src/yoetz/tui/runtime.py:1364-1370`, `src/yoetz/tui/runtime.py:1418-1426`, `src/yoetz/tui/runtime.py:516-550`, `src/yoetz/tui/app.py:1366-1374`

**Evidence.** `_consent_active` catches `Exception` and returns `False`. The layer is then `NOT_CONFIGURED`, whose word is `not configured` (`render.py:492`). The sibling `_policy_digest_layer` (`runtime.py:1446-1448`) returns `LayerState.UNKNOWN` on the same class of failure. Consent does not.

`privacy_recommendation` catches `Exception` from `recommended_privacy_recipe`, forces `private`, and then uses the `private` copy: "No current eligible exact provider route is configured, so this keeps network egress off entirely." The comment above the `except` says the failure is an unreadable `YOETZ_*` variable or file. `/privacy` prints that sentence as `Recommended:` (`app.py:1372-1374`). Fail-closed to private is the safe direction. The stated reason is a fact that was not observed.

Doctor has the same shape at a lower stake: `package_update_advisory` exceptions become `advisory = None` (`runtime.py:1802-1805`), and the version row stays `LayerState.VERIFIED` / `ok`. The `skipped_unavailable` outcome is the path that correctly becomes `UNPROVEN`. A raised error skips that path. `app.py:1002` and `1309` swallow the same exception for the tip and the upgrade offer; those two are silent by the docstring, the doctor row is not.

**Failure scenario.** Observation store throws (permissions, unexpected schema). `/status` details show `Project consent active … not configured` with the optional glyph, which reads as "you have not consented", not "consent could not be read". Separately, an unreadable provider config on `/privacy` recommends Private and asserts that no eligible route is configured. The user can approve a narrowing on that basis.

**Suggested fix and test.** Return a tri-state from the consent read; map exceptions to `LayerState.UNKNOWN` with detail `project consent could not be read`. On `recommended_privacy_recipe` failure, recommend `private` with reason `the provider configuration could not be read, so egress stays off`. In `doctor`, map a raised advisory to the existing `UNPROVEN` / "could not check for updates" row. Test `_consent_active` by monkeypatching `LocalObservationStore` to raise, and assert the layer state is `UNKNOWN`.

**Confidence:** high

**Related tests:** `tests/unit/tui/test_privacy_flow.py` stubs `privacy_recommendation` and never drives the `except` branch.

---

## U-09

- **Severity:** medium
- **Category:** blocking the UI thread
- **Title:** Header refresh and `/status` run host `--version` probes synchronously on the Textual loop
- **File:** `src/yoetz/tui/app.py:1010-1016`, `src/yoetz/tui/runtime.py:341-346`, `src/yoetz/adapters/integrations/host_discovery.py:33-40`, `src/yoetz/adapters/integrations/codex_discovery.py` (subprocess probes used by `discover_codex_binaries`)

**Evidence.** `_refresh_header` calls `self.runtime.discover_harnesses()` directly, then `mcp_state` per install. `status_snapshot` and `detect` do the same. `discover_harnesses` calls `discover_hosts()`, which calls `probe_version`: `subprocess.run((executable, "--version"), timeout=5)` on the calling thread. Codex discovery does its own `subprocess.run`. None of this is wrapped in `anyio.to_thread.run_sync`. Host connect preview does use `run_sync` (`app.py:1225`, `runtime.py:428`). The version probe does not.

`_consent_active`, `inspect_plugin`, and `check_policy_preview` are also synchronous inside `status_snapshot` (SQLite and filesystem) on that same loop. The control-channel awaits are not the blocker.

**Failure scenario.** Codex, Claude, and Cursor are installed and one `--version` hangs until the 5 second timeout. First paint, every `/status`, and every header refresh freeze key handling for the sum of those probes. Ctrl+C is not processed until the subprocesses return, because the loop is inside `subprocess.run`. The header is already showing the default privacy string for that whole interval (U-10).

**Suggested fix and test.** Run `discover_harnesses` via `run_sync` (one probe set per refresh, not per keystroke). Cache the last discovery for the session and refresh it from `/connect` and an explicit retry. Test that a `probe_version` which blocks is invoked on a worker thread by asserting the Textual pilot can still press `escape` while discovery is in progress, or by asserting `discover_harnesses` is scheduled with `run_sync`.

**Confidence:** high

**Related tests:** none. `tests/unit/tui/test_runtime.py` stubs discovery or runs it where no host binary blocks.

---

## U-10

- **Severity:** medium
- **Category:** honesty (privacy)
- **Title:** The session header claims "local only" before privacy is read, and keeps that claim if refresh fails
- **File:** `src/yoetz/tui/widgets/history.py:32`, `src/yoetz/tui/app.py:170-177`, `src/yoetz/tui/app.py:1033-1040`

**Evidence.** `SessionHeader` initializes `_privacy = "local only"` and paints it from `on_mount`. `YoetzTui.on_mount` sets version and project only. Privacy is updated at the end of `_refresh_header`, after discovery and `privacy_posture()`. The footer comment (`app.py:1043-1048`) says "ready" must not be shown before the service reports it. The header does not follow that rule for privacy: `local only` is the optimistic default (`PrivacyChoice.LOCAL_ONLY` is the actual closed profile, so this string is a real posture, not a placeholder like `unknown`).

`_refresh_header` has no `except` around `discover_harnesses()` or `privacy_posture()` beyond what those functions catch. An unexpected exception leaves `_privacy` at `local only` for the rest of the session. `privacy_posture` itself returns `readable=False` / summary `unknown` on `ControlError`, which is the honest result when that path is reached.

**Failure scenario.** Effective policy is assisted review. From startup until the version probes in U-09 finish, the header reads `local only`. If discovery raises, it keeps reading `local only` while egress is permitted. The footer stays blank until a successful refresh, so the header is the only privacy claim on screen.

**Suggested fix and test.** Default `_privacy` to `unknown` and do not paint a profile name until `privacy_posture` returns. On refresh failure, set privacy to `unknown` and harness to `unknown`. Test a runtime whose `privacy_posture` is `assisted_review` and whose `discover_harnesses` blocks, and assert the first header paint is not `local only`.

**Confidence:** high

**Related tests:** none for the initial header string. `tests/unit/tui/test_render.py::test_status_makes_no_privacy_claim_when_the_policy_cannot_be_read` covers `render_status` only.

---

## U-11

- **Severity:** medium
- **Category:** honesty (integration status)
- **Title:** The header is "connected" if any host is, but `/status` reports only the first discovered install
- **File:** `src/yoetz/tui/app.py:1014-1032`, `src/yoetz/tui/runtime.py:1264-1277`, `src/yoetz/adapters/integrations/host_discovery.py:82-98`

**Evidence.** `_refresh_header` records `mcp_state` for every harness and sets the header to `connected` if any value is `yoetz_owned`. `status_snapshot` calls `mcp_state(harnesses[0])` only. `discover_hosts` puts Codex binaries first, then Claude, then Cursor. For a non-Codex host, `mcp_state` returns `yoetz_owned` when installed and configured (`runtime.py:398-407`), and the header treats that the same as a Codex-owned MCP entry.

**Failure scenario.** Codex is installed but not connected, and Claude is configured. The header says `connected`. `/status` inspects Codex, the first row, and shows project integration `not configured` or, if that Codex entry is `foreign_present`, `blocked`. The two surfaces disagree about whether this project is connected, and about which host.

**Suggested fix and test.** Build the MCP layers from the same aggregation as the header, and name the host in both (`Codex: not connected`, `Claude: connected`). Do not let one host's `yoetz_owned` paint the Codex column. Test `status_snapshot` with a two-install fake where only the second is `yoetz_owned`, and assert the status integration row matches the header.

**Confidence:** high

**Related tests:** none for multi-host aggregation. `tests/unit/tui/test_agent_route_layer.py` covers the Codex route layer in isolation.

---

## U-12

- **Severity:** low
- **Category:** performance (unbounded widget tree)
- **Title:** The transcript model drops events after 500, and the history widget keeps every one
- **File:** `src/yoetz/tui/events.py:46-53`, `src/yoetz/tui/widgets/history.py:99-102`

**Evidence.** `Transcript.append` deletes from the front once `len(events) > limit` (default 500). `History.append` always `mount`s a new `_EventWidget` and never removes the widgets that correspond to dropped events. `replace_last` updates `widgets.last()`, which stays aligned with `events[-1]`, so the last line is not written onto the wrong widget. The screen still holds every prior widget. `latest_with_details` walks the bounded model, so `D` can open a different event than an older on-screen row that has already been dropped from the model.

Layout cost grows with mounted event widgets (`height: auto` in `styles.py:60-63`), not with the 500-event cap the model documents.

**Failure scenario.** A long session of `/status`, `/doctor`, and paged lineage views passes 500 transcript lines. Memory and layout keep every widget. Pressing `D` uses the trimmed model and can show details for a newer event than the one still visible at the top.

**Suggested fix and test.** When the model drops from the front, `remove()` the same number of leading `_EventWidget`s. Test by appending `limit + 5` events and asserting `len(history.query(_EventWidget)) == limit`.

**Confidence:** high

**Related tests:** none.

---

## Coverage notes

| Area | Result |
| --- | --- |
| Secret display | No finding. Ceremony handoff is the only secret path; `password=True` is unused. |
| Runaway refresh | No finding. Resize redraws are idempotent. |
| O(n^2) layout | `SelectionView.filter` is O(n^2) membership over the option list (`views.py:232-240`). n is the provider/command list, not the transcript. Not reported as a defect. |
| Socket IPC on the UI thread | Not observed. `ServiceClient._send` awaits `write_control_frame`. |
| `render_provider_failure` | Defined and unit-tested, never called from `app.py`. Dead copy, not a live false claim. |
| `ReceiptSummary.not_verified` | `build_receipt` always sets `not_verified` to "external AI-powered review did not contribute" (`runtime.py:1725-1726`) even when `semantic_available` is true. `render_receipt` shows `rendered_lines` first (`render.py:658-659`), and both the JSON and human renderers produce lines, so this string is not what `/receipt` displays. Not filed as a user-visible bug. |


---

# Config, paths, and runtime

Source notes: `17-config-runtime.md`.

# Audit 17 — config, runtime isolation, git subject state, object files

Read-only review of `src/yoetz/config/`, `src/yoetz/adapters/runtime.py`, `src/yoetz/adapters/git_subject_state.py`, `src/yoetz/adapters/objects/`, in-memory doubles under `src/yoetz/adapters/memory/`, and the ledger / importer / semantic ports. No source files were modified. No live state directory was opened.

Git command execution was probed with Git 2.43.0 under the adapter's `-c` prefix and cleaned environment (`alias.status`, `alias.rev-parse`, `alias.ls-files`, `alias.diff`, and a `diff.*.textconv` driver). Builtin commands were not replaced by shell aliases. `--no-textconv` stopped the textconv canary; omitting that flag ran it. That class is not filed.

## Findings

### F-01 — Network-filesystem refusal fails open when the probe cannot decide

- **Severity:** medium
- **Category:** security
- **Title:** Private-bundle network check returns success when mount detection fails
- **Location:** `src/yoetz/config/paths.py:573-596` (diagnostic sink `src/yoetz/config/paths.py:140-141`)
- **Confidence:** high

**Evidence.** `_check_network_filesystem` is the gate ADR-026 and issue #723 use to refuse `nfs`, `9p`, `virtiofs`, and the rest of `_NETWORK_FILESYSTEMS_LINUX` / `_NETWORK_FILESYSTEMS_MACOS`. On any exception from `os.statvfs` or `probe.mount_table()` it records `network_filesystem_probe_failed` and returns. On macOS, `macos_fstype` returning `None` takes the same path. The production probe's `diagnostic` is `_ignore_diagnostic`, which discards the token. An empty mount table is not an exception: `_linux_mount_fstype` returns `None`, `None` is not in the denylist, and the function returns. A present table still refuses the longest matching denylisted fstype (`tests/unit/config/test_paths.py`).

**Failure scenario.** `/proc/mounts` is unreadable, empty, or `statvfs` raises (restricted container, LSM denial, transient I/O error). `verify_private_local_bundle` then accepts a directory that sits on NFS, CIFS, 9p, or virtiofs. The vault, catalog, and ledgers are created on a filesystem the product refuses because locking and crash durability are uncertified. The caller sees success, not `path_on_network_filesystem`.

**Fix and test.** Treat an unknown filesystem as unsafe. If `statvfs`, the mount table, or `statfs` fails, or no mount covers the path, raise `PathSafetyError("path_on_network_filesystem")` (or a dedicated `network_filesystem_probe_failed` reason if operators must distinguish "denied" from "unknown"). Do not depend on the diagnostic callback for the decision. Add a unit test whose `mount_table` raises `OSError`, one whose table is `""`, and one whose macOS fstype callback returns `None`. Each must raise. Keep the existing denylist tests for a readable table that names `nfs`, `9p`, `drvfs`, and `virtiofs`.

**Related tests.** `tests/unit/config/test_paths.py` (`test_ordered_symlink_permission_repository_sync_and_network_reasons`, `test_windows_drive_and_vm_share_transports_are_refused_like_a_network_share`). None cover probe failure.

### F-02 — Owner and mode checks skip missing paths, swallow stat failures, and ignore ancestors

- **Severity:** medium
- **Category:** security
- **Title:** Bundle permission gate is leaf-only and fails open when POSIX facts are unavailable
- **Location:** `src/yoetz/config/paths.py:471-487`, `src/yoetz/config/paths.py:637-666`
- **Confidence:** high

**Evidence.** `_check_owner_and_mode` returns immediately when `path.exists()` is false, so a not-yet-created `data_dir` is not checked and neither are its parents. If `path.stat()` raises `OSError`, or `stat.S_IMODE` raises, it emits `path_posix_facts_unavailable` / `path_mode_unavailable` through the same no-op diagnostic and returns. It never walks ancestors. `ensure_owner_only_dir` rejects a symlink only on components that already exist at the start of the call, creates missing directories at `0o700`, then `lstat`s the final path. `lstat` does not notice an intermediate symlink. A parent at `0o777` with a child at `0o700` satisfies both functions.

**Failure scenario.** Another local user can write an ancestor (mode `0o777`, or a group-writable shared parent outside the fixed temp denylist). They rename the `0o700` leaf and put a directory symlink in its place after `verify_private_local_bundle` returns. Later `open`/`mkdir` follow that symlink into a directory the check never saw. The same window exists inside `ensure_owner_only_dir` between the component walk and `mkdir`: a missing component created as a symlink is followed by `mkdir`, and the final `lstat` sees the target directory's `0o700` mode.

**Fix and test.** Fail closed when owner or mode cannot be read for an existing path (`path_not_owned` / `permissions_too_broad`), instead of returning. For every existing ancestor, require the caller uid and reject mode bits `0o022` (group/world write), not only the leaf. Hold a directory fd from the trusted ancestor and create children with `mkdirat`/`openat` and `O_NOFOLLOW` so the post-check path cannot be swapped. Re-run `_reject_symlink_components` on the final path after creation. Tests: parent `0o777` + child `0o700` must raise; `stat` patched to raise `OSError` on an existing directory must raise; a symlink planted at a missing intermediate before `mkdir` must raise `path_contains_symlink`.

**Related tests.** `tests/unit/config/test_paths.py` checks the leaf (`0o750` → `permissions_too_broad`, uid mismatch → `path_not_owned`) and symlink rejection of the leaf. No ancestor-mode or probe-failure test.

### F-03 — Runtime pin and instance identity use check-then-open, and identity create can clobber

- **Severity:** medium
- **Category:** security
- **Title:** Private pin and instance-identity reads follow a replaced symlink; identity create is not exclusive
- **Location:** `src/yoetz/config/paths.py:209-253`, `src/yoetz/config/installation.py:272-285`, `src/yoetz/config/installation.py:338-378`, `src/yoetz/config/installation.py:423-457`
- **Confidence:** high on the race in the code; medium on cross-user exploitability (needs a writable parent)

**Evidence.** `read_runtime_pin` `lstat`s, rejects non-regular files, a foreign uid, mode `& 0o022`, and oversize files, then calls `path.read_bytes()`. That open follows a symlink planted after the `lstat`. `_read_private_file` does the same: `lstat` rejects mode `& 0o077`, then `path.open("rb")`. `write_instance_identity` refuses when `path.exists() or path.is_symlink()`, then `_write_private_atomic` `os.replace`s onto that path. `replace` overwrites an existing regular file. Two callers that both pass the exists check both publish, and the second seal wins. `write_runtime_pin` is closer to safe on the temp file (`O_EXCL | O_NOFOLLOW`) but still decides "same root" via `path.is_symlink()` plus `path.read_bytes()`, and a same-root pin is replaced even when `installation_id` differs (`installation.py:432-433`).

**Failure scenario.** The runtime prefix or state directory is group-writable (shared venv, umask `0002`, or a prefix that was not created `0o700`). After the victim process `lstat`s its `0600` pin, the other user replaces it with a symlink to a pin document they wrote. The victim reads that document. `_validated_isolated_root` still requires the named root to be an existing owner-only directory of the victim, so the attacker names the victim's ambient data directory (itself a valid private bundle). A pinned runtime then opens the live catalog while its lock stays under a different state directory. Separately, two overlapping `write_instance_identity` calls replace a sealed installation id; `verify_instance_binding` then sees a pin/marker mismatch or serves the winner's identity. The sequential `instance_exists` test does not cover the overlap.

**Fix and test.** Open the pin and the identity file with `O_NOFOLLOW | O_CLOEXEC` (and `O_NOFOLLOW` on the parent via `openat`), then `fstat` the fd and apply the mode/owner/size checks to that inode before `read`. For create, use `O_CREAT | O_EXCL` on the final name, or `linkat` the temp into place and treat `EEXIST` as `instance_exists`, instead of `exists` plus `replace`. If an existing runtime pin names the same root, require the same `installation_id` or keep `runtime_pin_conflict`. Tests: (1) file swapped for a symlink after a successful `lstat` is not followed — easiest as an `O_NOFOLLOW` unit test that plants the symlink first and asserts `runtime_pin_invalid` / `instance_identity_invalid`; (2) two threads calling `write_instance_identity` on an absent path, exactly one success, the file bytes equal to the winner, the loser `instance_exists`.

**Related tests.** `tests/unit/config/test_instance_identity.py` (sequential `instance_exists`), `tests/unit/config/test_isolated_root.py` (pin/root conflict when both names are stable). No symlink-swap or concurrent-create test.

### F-04 — Non-default config and privacy desired-state writes are not owner-only

- **Severity:** medium
- **Category:** security
- **Title:** Explicit config and privacy desired-state paths follow symlinks and inherit umask
- **Location:** `src/yoetz/config/write.py:637-648`, `src/yoetz/config/write.py:710-722`, `src/yoetz/config/load.py:187-194`, `src/yoetz/config/privacy_desired.py:77-90`
- **Confidence:** high

**Evidence.** The default config path goes through `ensure_owner_only_dir` and the atomic writer uses `mkstemp` plus `chmod 0o600`. An explicit path does `target.parent.mkdir(parents=True, exist_ok=True)` with the default mode `0o777` masked by umask, and does not walk symlink components. `mkdir` follows an existing symlink parent. `_atomic_write_config` then `chmod`s the temp name; `chmod` follows a symlink if the temp directory entry is replaced in a non-sticky writable parent. `load_config` / `_read_config` open the selected path with `Path.open`, which follows symlinks, and never checks owner or mode. `write_privacy_desired_toml` is the exporter used by `privacy export-desired`: `mkdir(parents=True)` and `write_text` (create mode `0o666` masked by umask, so `0644` under umask `022`). `load_privacy_desired_canonical` likewise `read_text`s whatever the path names. The runtime pin and instance identity files reject group/world-writable mode; these two do not.

**Failure scenario.** `YOETZ_CONFIG` or a CLI `--config` path has a parent that is a symlink into a shared directory, or the parent is created `0755`/`0777`. Another user who can write that directory replaces `config.toml` with a document that sets `verification.semantic` to `disabled`, points `storage.data_dir` at another owner-only directory of the victim, or sets `[provider.owner_declared_endpoint].https_origin` at an attacker host. The loader accepts it. A privacy export written with the default umask is world-readable and, if the destination name is a symlink, `write_text` updates the symlink target.

**Fix and test.** Run the same symlink walk and `0o700` directory creation for every config parent, not only the platform default. Open the config and the privacy file with `O_NOFOLLOW`, reject mode `& 0o022` (config) or `& 0o077` (privacy), and write via `O_EXCL` plus `fchmod` on the fd, then `rename` over a non-symlink destination. Reject a symlink at load time with `config_file_unreadable` or a path-safety reason. Tests: explicit path whose parent is a symlink is refused; a `0664` config is refused; `write_privacy_desired_toml` leaves a `0600` regular file and does not follow a pre-existing symlink.

**Related tests.** Config round-trip and precedence tests under `tests/unit/config/test_load_precedence.py` and the writer tests that accompany `write_config_toml`. They do not assert mode or symlink refusal for an explicit path. `tests/unit/config/test_paths.py` covers `ensure_owner_only_dir` only.

### F-05 — One operation can own two semantic jobs; load picks one silently

- **Severity:** medium
- **Category:** correctness
- **Title:** Memory ledger load of a semantic job fails open when case digests diverge
- **Location:** `src/yoetz/adapters/memory/ledger.py:2422-2454`, `src/yoetz/adapters/memory/ledger.py:2733-2746`
- **Confidence:** high

**Evidence.** This adapter is the production state machine: `src/yoetz/adapters/sqlite/repository.py` mutates and loads through `MemoryLedgerAdapter`. `enqueue_semantic_job` keys `job_by_case` by `(writer_id, operation_id, case_digest)`. A second call with the same operation and a different digest allocates another job. `load_semantic_job` collects every job for that operation and returns `max` by `(attempt_count, job_id)`. The comment says multiple case digests "should not" exist; enqueue allows them. Callers in `src/yoetz/service/ready_composition.py` (around the `load_semantic_job` uses) and `src/yoetz/application/semantic_attempts.py` treat that single record as the job.

**Failure scenario.** A check enqueues a job for case digest A, then a retry enqueues digest B (new frontier or rebuilt case) without failing the first. Recovery loads whichever job sorts higher. The other job stays `queued`. The resumed attempt can bind a different case object than the lease the caller is holding, or a later claim can run the leftover job. Tests that enqueue once never see the choice.

**Fix and test.** On enqueue, if any job already exists for `(writer_id, operation_id)` with a different `case_digest` or a live state, raise the existing pending/conflict error instead of inserting. `load_semantic_job` should return that single job or raise `STORAGE_CORRUPT` when the set size is not 1. Test: enqueue two digests for one operation; the second call raises; `load_semantic_job` never returns a job that was not the sole row.

**Related tests.** Ledger unit tests that cover enqueue/claim replay for a single digest. No test asserts one operation, two digests.

### F-06 — Direct memory append and the SQLite wrapper disagree on a live duplicate

- **Severity:** low
- **Category:** correctness
- **Title:** In-memory append reports a retryable pending operation where SQLite reports corruption
- **Location:** `src/yoetz/adapters/memory/ledger.py:1600-1608`, `src/yoetz/adapters/sqlite/repository.py:1861-1869`
- **Confidence:** high on the divergence; medium that the memory result is what tests assert

**Evidence.** `MemoryLedgerAdapter.append_batch`, when the operation id is already stored, replays only a `COMPLETE` result and otherwise raises `OPERATION_PENDING` (retryable). The SQLite adapter, before it calls that oracle, loads the durable operation row and raises `STORAGE_CORRUPT` unless the row is `complete` and the in-memory cache still holds the result. A pending or cache-missing duplicate never reaches the oracle.

**Failure scenario.** Unit tests and any caller that mounts `MemoryLedgerAdapter` directly treat a replay of an in-flight operation as retryable `OPERATION_PENDING`. The same bytes against the production repository are a non-retryable `STORAGE_CORRUPT`. A client or test written against the double will not catch a production hard failure, and a retry loop tuned to the double will not match the service.

**Fix and test.** Make the pre-check and the oracle share one outcome. If a pending duplicate is retryable, the SQLite wrapper should raise `OPERATION_PENDING` and not `STORAGE_CORRUPT`. If a durable row without a cached result is corrupt, the memory adapter should raise `STORAGE_CORRUPT` for that shape too. One parameterized test should run the same duplicate append against the memory adapter and a temporary SQLite repository and expect the same public code.

**Related tests.** Memory ledger append/idempotency tests and `tests` that drive `sqlite/repository.py` append. They are not asserted against each other for this branch.

## Reviewed and not filed

- **Isolation variable fail-closed.** A set but empty, relative, missing, or non-directory `YOETZ_ISOLATED_ROOT` raises `isolation_root_invalid`. A pin that names a different path raises `isolation_root_conflict`. There is no fallback to the ambient platform directories while the variable is set (`paths.py:272-301`). ADR-026 and `tests/unit/config/test_isolated_root.py` lock this.
- **Explicit `storage.data_dir` overrides `<root>/data`.** `bundle_root(_data_dir=...)` returns the override after `verify_private_local_bundle`, including when isolation is on. ADR-026 decision 4 and `test_explicit_data_dir_override_still_wins_over_the_isolated_default` call that storage relocation, not isolation, and leave shared-storage detection to the parity gate. Not reclassified as a defect.
- **Omitted verification leaves on an existing file.** `load_config` injects `semantic = "optional"` and `max_findings = 3` when a readable file omits them (`load.py:330-337`). A missing file keeps the model default `required` / `10` (`models.py:317-319`). `test_new_defaults_preserve_omitted_existing_leaves_and_explicit_migration` locks the split. It is compatibility, not an accidental merge. Residual footgun: any readable file, not only a pre-migration one, gets the weaker semantic value.
- **Git argv injection.** Arguments passed to Git are literals plus the fixed `_GIT_SAFE_PREFIX`. The environment replaces the parent environment (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `core.hooksPath=/dev/null`, `diff.external=`). Relative paths from NUL output are rejected when they are absolute or contain `.` / `..` (`git_subject_state.py:991-998`). Untracked files are opened with `dir_fd` and `O_NOFOLLOW`; a non-regular file becomes `SYMLINK_UNSUPPORTED`. `.git` must be a real directory; `objects/info/alternates` is refused; `[include` and `[filter` in `.git/config` are refused. The Git 2.43 probe above did not execute a builtin alias or a textconv helper.
- **Object paths.** Object ids are `obj_` plus a UUID (`protocol/ids.py`). Shard directories and frames are created `0o700` / `0o600`, opened with `O_NOFOLLOW`, and rejected when the inode is not a regular nlink-1 file owned by the euid with mode `& 0o077`. `os.replace` of a staged frame does not follow a final symlink. Sweep skips a shard that fails the private-directory check instead of unlinking through it (`encrypted_files.py:696-702`); that is conservative, not a write outside the store.
- **Ports.** `LedgerPort`, `ImporterPort`, and `SemanticEvaluatorPort` are structural protocols. The value types in those modules reject malformed ids, digests, frontiers, and semantic status/reason pairs in `__post_init__`. No default on those types turns verification off or points egress at an implicit host. `ImportCaptureInput` bounds argv and working-directory text and rejects NUL/CR/LF. Memory import treats an over-size capture as `LIMIT_EXCEEDED` (`memory/importer.py:358-362`), not a truncated success.
- **`LocalBundleRuntime` read facade.** `_ReadLedger` omits mutators. `has_active_frozen_case` is invoked from publish, which routes `RouteAccess.WRITE` and therefore receives the real ledger (`application/publish_work.py:1505`, `runtime.py:1153-1156`). Not a missing-method production failure on the path that calls it.


---

# Migrations and scripts

Source notes: `18-migrations-scripts.md`.

# Audit 18 — migrations, scripts, support helpers, packaging entry points

Read-only review of `migrations/`, `scripts/`, `support/`, packaging entry points in `pyproject.toml`, and `tests/packaging/` as invariant documentation. Packaging tests were not executed. No `shell=True` or `os.system` appears in `scripts/`. The npm launcher uses `spawnSync(..., { shell: false })`.

Migration SQL is applied by `run_migrations` (`src/yoetz/adapters/sqlite/migrations.py`) inside one APSW `with db:` transaction. A failing statement rolls that transaction back, including `user_version`. `tests/integration/storage/test_migration_0003_observation.py` (`test_failed_followup_migration_rolls_back_atomically`) and `tests/integration/storage/test_catalog_v4_extensions.py` lock that. There is no down-migration SQL; `docs/runbooks/migration-rollback.md` defines rollback as restore of a verified pre-migration backup, and section 9 prohibits reverse SQL. That policy is not itself a defect.

Table rebuilds in `migrations/bundle/0009.sql`, `migrations/bundle/0014.sql`, and `migrations/catalog/0004.sql` (`start_operations`) copy rows because SQLite cannot widen a `CHECK` in place. Column lists match the source tables, and `0014` restores the same `events_*` indexes, including `UNIQUE` `events_payload_object`. Those copies are required, not spare full-table rewrites. They do hold one exclusive transaction that temporarily stores both the old and new tables (including `events.canonical_entry` blobs) until commit. Bundle upgrades from schemas 12, 13, and 14 take a machine-bound backup before that DDL (`src/yoetz/service/bundle_upgrade.py`, ADR-003).

`scripts/verify_resource_manifest.py` is the only copier of canonical resources into `src/yoetz/resources/`. It refuses symlinks, stages, then `os.replace`s. `tests/integration/storage/test_migration_0001.py` (`test_root_and_installed_migration_resources_are_byte_identical`) compares every registry version’s repo-root SQL to the package mirror. `scripts/sync_resource_ripple.py` calls that sync on the write path and fails closed if the owned-byte digest does not converge. No ripple bug was found that would publish a stale migration while `--check` stays green.

## Findings

### G-01

- **Severity:** medium
- **Category:** missing rollback / durability
- **Title:** Catalog startup applies 0002–0005 with no pre-migration backup
- **File:lines:** `src/yoetz/service/ready_composition.py:1620-1633`; `src/yoetz/adapters/sqlite/migrations.py:345-353` and `396-407`; `migrations/catalog/0004.sql:26-31` and `173-177`; `docs/storage-ownership.md:181-195`
- **Evidence:** `open_ready_catalog` opens `_open_catalog_migration_writer` and calls `run_migrations(..., maintenance=None)`. `run_migrations` does `del maintenance` and always returns `backup_manifest_digest=None`. Catalog `0004` rewrites `start_operations` (`ALTER` rename, copy, `DROP TABLE start_operations_v3`), backfills `lineage_digest` from `active_route_identity_digest`, and inserts every existing route into `task_sessions` as `health='contact_lost'`. `docs/storage-ownership.md` states this path does not create the machine-bound backup used for task bundles, and that backup parity remains under #496. `tests/integration/storage/test_catalog_v4_extensions.py` asserts `report.backup_manifest_digest is None` after a successful 0004/0005 apply.
- **Failure scenario:** A catalog at schema 1–3 is upgraded on service startup. The SQLite transaction commits a bad but constraint-valid backfill (every session forced to `contact_lost`, lineage digests rewritten, `start_operations` rebuilt). There is no backup manifest and no phase journal. The next start sees `user_version` 5 via `verify_schema_identity`, which does not re-run `PRAGMA foreign_key_check`, and treats the catalog as current. Bundle upgrades that fail after commit quarantine `rollback_required` and keep the backup (`src/yoetz/service/bundle_upgrade.py:1392-1416`). Catalog startup has no equivalent.
- **Suggested fix and test:** Before catalog DDL, take the same verified machine-bound backup the bundle coordinator uses, and record its digest on a catalog maintenance row. If backup or post-commit identity/`foreign_key_check` fails, leave the original catalog file untouched (restore from that backup) and refuse READY. Do not report success with a null backup digest. Test: seed a schema-3 catalog with two routes, fault the process after `0004` commits and before the writer reopen, and assert either the pre-image bytes are still what the next start opens or a backup digest names a restorable snapshot whose routes still have the original session rows. Extend `test_catalog_v4_extensions.py` so a null backup digest fails once the backup exists. The `contact_lost` backfill itself matches the storage-ownership note and should stay; the gap is the missing restore point.
- **Confidence:** high
- **Related tests:** `tests/integration/storage/test_catalog_v4_extensions.py`; `tests/integration/storage/test_migration_0003_repository_privacy.py`; `tests/integration/storage/test_migration_rollback.py` (journal quarantine only, not catalog startup)

### G-02

- **Severity:** medium
- **Category:** unsanitized paths / writes outside the intended root
- **Title:** Test-instance base checks do not resolve symlinks, then chmod and rmtree that path
- **File:lines:** `scripts/provision_test_instance.py:97-105`, `211-222`, `278-279`, `299-337`
- **Evidence:** `_validate_base` requires an absolute path, rejects lexical ancestors that contain `.git` / `.hg` / `.svn` / `.jj`, and rejects `Path.is_relative_to` of `/tmp` or `/private/tmp`. It never `resolve()`s. `command_create` then `base.mkdir`, `os.chmod(base, 0o700)` (follows symlinks), and on any failure `shutil.rmtree(layout["root"], ignore_errors=True)`. `command_dispose` rmtrees `layout["root"]` after `yoetz instance dispose`. Tag characters cannot escape the base (`_TAG_ALPHABET` has no slash or dot), so the escape is the base itself.
- **Failure scenario:** `--base` is an absolute symlink whose lexical path is outside `/tmp` and whose parents are not a checkout, while the target is `/tmp/...`, a subdirectory of a repo (so `base/.git` does not exist), or the live state directory. `chmod 0700` applies to the symlink target. Create/dispose then write and delete `<target>/<tag>`. The `/tmp` ban and the in-repo ban both miss this shape. `docs/runbooks` and the module docstring say the script does not touch the everyday install; that holds only for a non-symlinked base.
- **Suggested fix and test:** `resolve(strict=False)` the base and every ancestor before the repository and temp checks; reject if any component is a symlink (`Path.is_symlink()` on each part, not only the final path). Refuse the resolved path when it is `/tmp`, `/private/tmp`, inside a VCS checkout, or equal to `yoetz.config.paths.state_dir()`. Use `os.open(..., O_NOFOLLOW)` or `chmod` via a dir fd for the mode change. On the failure path, `rmtree` only after confirming the directory inode is the one `mkdir` created and is not a symlink. Test with `tmp_path` symlinks: base → directory under a fake repo subfolder, base → a fake state dir, and assert `create` exits `base_in_repository` or `base_shared_temp` without creating children or changing the target’s mode.
- **Confidence:** high
- **Related tests:** none in `tests/packaging/` (that tree was not executed). Instance path rules live in product code (`src/yoetz/config/paths.py` symlink rejection) and are not applied to this script’s `--base`.

### G-03

- **Severity:** medium
- **Category:** supply chain
- **Title:** Test-instance install retries `uv pip install` online after an offline failure
- **File:lines:** `scripts/provision_test_instance.py:165-174`
- **Evidence:** `_install_runtime` builds a local wheel with `uv build --no-sources`, then `uv pip install --offline`. If that returns non-zero, it runs the same install without `--offline` and without `--require-hashes`, `--no-index`, or `--find-links` bound to `uv.lock`. `tests/packaging/test_release_workflow_contract.py:158-171` requires the published-wheel smoke to use `--no-index --no-deps --find-links` on the approved artifact. This script’s fallback does not.
- **Failure scenario:** The offline install fails because a dependency is missing from the local cache (or because `--offline` is rejected). The retry resolves `yoetz`’s pinned dependencies from whatever indexes the ambient `uv` configuration trusts and installs those distributions into the instance runtime. A substituted index can serve the declared version with different bytes. The instance launcher is then treated as the isolated runtime.
- **Suggested fix and test:** Keep a single install command: `uv pip install --offline --require-hashes` against an exported hash set from `uv.lock`, or `--no-index --find-links <wheel dir>` when the wheel vendor directory is complete. If offline install fails, exit `runtime_install_failed` and print the captured stderr; do not open the network. Test by pointing `UV_INDEX` at an empty or hostile local index, forcing the offline attempt to fail, and asserting the second `uv` invocation is absent and the runtime directory is removed.
- **Confidence:** high
- **Related tests:** `tests/packaging/test_release_workflow_contract.py` (`test_post_publication_smoke_uses_python314_and_reinstalls_the_approved_wheel_offline`); `tests/packaging/test_dependency_lock_and_licenses.py` (lock digest is a release gate, not this script)

### G-04

- **Severity:** medium
- **Category:** writes outside the intended root / missing reverse path
- **Title:** Dogfood lane mutates the real host config by default and never disconnects it
- **File:lines:** `scripts/dogfood_ci/lane.py:26-31`, `260-266`, `573`, `789-805`, `826-875`, `1806-1845`
- **Evidence:** The module docstring says to run on a throwaway runner and that nothing touches an everyday installation. The default `host_config_root` is `Path.home() / {".codex", ".claude", ".cursor"}` when `--host-config-root` is omitted. The documented command in the same docstring does not pass that flag. `phase_install` does `host_config_root.mkdir`. If `FIREWORKS_API_KEY` is set, `_write_codex_provider_config` reads `config.toml` and writes a new file that prepends a Fireworks `model` / `model_provider` block. `phase_connect` runs `yoetz setup run --accept` against that root. `phase_teardown` disposes the pinned Yoetz instance and deletes `plugin_dir`. It never calls setup disconnect, and it does not restore `config.toml`.
- **Failure scenario:** The documented command is run on a workstation that already has Codex. `~/.codex` is created or updated, `config.toml` gains a leading model override (the guard only skips when `[model_providers.fireworks]` is already present), and host hooks/MCP stay registered after the instance is deleted. A later `codex` session uses the Fireworks provider block and the Yoetz integration whose runtime was just removed.
- **Suggested fix and test:** Default `--host-config-root` to `<base>/<tag>-host` (mode 0700), not the home directory. Require an explicit `--allow-live-host-config` to use `~/.codex`, `~/.claude`, or `~/.cursor`. Copy `config.toml` aside before prepending, and on teardown run the host disconnect/remove path and restore the copy. Test with `HOME` set to a temporary directory that already contains `.codex/config.toml` with a `model` key: after `Lane.run` (connection mode `setup-run`, launcher shimmed), the original file bytes are unchanged unless the allow flag is set, and with the flag, teardown returns those bytes.
- **Confidence:** high
- **Related tests:** none under `tests/packaging/` for this script. Host disconnect exists on the product side (`docs/usage/providers.md` rollback/disconnect); this lane does not call it.

### G-05

- **Severity:** low
- **Category:** supply chain
- **Title:** npm launcher installs unhashed PyPI `yoetz==<version>` via ambient `uv` on `PATH`
- **File:lines:** `support/npm-launcher/bin/yoetz.js:21-23`, `36-47`, `73-101`; `support/npm-launcher/package.json:19-25`
- **Evidence:** `spawnSync("uv", ...)` and `spawnSync("uvx", ...)` use `shell: false` and forward `process.argv` unchanged. The install argv is `tool install --quiet --python 3.14 yoetz==${version}` with `version` from `package.json`. There is no `--require-hashes`, index pin, or artifact digest. On `ENOENT`, stderr tells the operator to run `curl -LsSf https://astral.sh/uv/install.sh | sh` (and the Windows `irm | iex` equivalent). The launcher does not execute that pipeline. `tests/packaging/test_npm_launcher.py` freezes the exact install and `uvx --python 3.14 yoetz ...` argv, forbids npm lifecycle `scripts` and runtime dependencies, and expects `astral.sh/uv` in the missing-`uv` message. `tests/packaging/test_release_workflow_contract.py:107-117` and `:166` lock the same unhashed install in the release verifier and forbid that verifier from running the curl installer.
- **Failure scenario:** `npx yoetz` finds a `uv` earlier on `PATH`, or an ambient `uv` config adds an extra index that serves `yoetz==0.3.0` with different bytes. `uv tool install` persists that build, and later host hooks bind its absolute launcher path. Separately, an operator following the printed line pipes a remote shell script to `sh`. Both are outside the launcher’s “downloads nothing itself” claim: the download is delegated, and the suggested uv bootstrap is an unauthenticated pipe.
- **Suggested fix and test:** Resolve `uv` from an explicit path or reject a `uv` whose `--version` is not the pinned `0.11.29` from `pyproject.toml` `[tool.uv]`. Pass `--require-hashes` with the release wheel digest already computed in the npm publish job (`NPM_SHA256SUMS` covers the npm tarball, not the PyPI artifact). Point the missing-`uv` text at `https://docs.astral.sh/uv/getting-started/installation/` only, which the file already includes, and drop the pipe-to-shell lines. Update `test_launcher_delegates_to_pinned_uvx` and `test_npm_release_is_built_once_published_after_pypi_and_download_verified` in the same change so the frozen argv matches the hashed install.
- **Confidence:** high that the argv and the curl text are what ship; medium that an extra index is a practical substitute (it depends on the operator’s uv config, which this repo does not set)
- **Related tests:** `tests/packaging/test_npm_launcher.py`; `tests/packaging/test_release_workflow_contract.py`

### G-06

- **Severity:** low
- **Category:** unsanitized paths
- **Title:** Release-evidence hashing accepts artifact paths outside the manifest directory
- **File:lines:** `scripts/generate_release_evidence.py:158-173` and `191-194`; contrast `scripts/build_release_inputs.py:94-98` and `150-152`
- **Evidence:** `load_and_validate_inputs` sets `artifact_path = root / relative_path` and checks only `is_symlink()` and `is_file()` on that final path. `hash_artifacts` reads those bytes. `Path` join drops `root` when `relative_path` is absolute, and `..` escapes `root`. A symlink on an intermediate directory is followed because only the final path is classified as a symlink. `build_release_inputs._path_within` resolves and requires `relative_to(root)` before it will emit a path, so the producer is tighter than this consumer.
- **Failure scenario:** A manifest (hand-written, or a workflow input that skipped the builder) sets `artifacts[].path` to `../../some-file` or an absolute path, with the matching sha256. The generator reads that file, and on digest match copies the path into the evidence document and checksum list. Parent-directory symlinks under an otherwise local relative path do the same.
- **Suggested fix and test:** Reject absolute paths, `..`, and backslashes. Require `path.resolve().relative_to(root.resolve())` and reject if any ancestor of the resolved file, from `root` down, is a symlink. Test with a manifest that points at `../outside.bin` and at a symlink directory inside the manifest root; both must raise `artifact_missing` or a dedicated `artifact_escapes_root` before `read_bytes`.
- **Confidence:** high
- **Related tests:** packaging coverage of the release workflow asserts artifact checksum gates (`tests/packaging/test_release_workflow_contract.py`) but not this path check

### G-07

- **Severity:** low
- **Category:** migration diagnostics
- **Title:** Startup labels every pre-v12 bundle `schema_upgrade_path_unknown` and never reaches the v10 layout check
- **File:lines:** `src/yoetz/service/ready_composition.py:840-869` and `1196-1203`; `src/yoetz/service/bundle_upgrade.py:79-82` and `1280-1307`; `docs/adr/ADR-003-storage-sqlite-durability.md:151-161` and `197-200`
- **Evidence:** `BUNDLE_UPGRADE_SOURCE_VERSION` is 12 and `BUNDLE_UPGRADE_SOURCE_VERSIONS` is `(12, 13, 14)`. Startup inventory, for `schema_version < 12`, records `BundleUpgradeReason.SCHEMA_UPGRADE_PATH_UNKNOWN` and `continue`s without adding a target. The comment says v9–v11 are deliberately left untouched so other routes can still become READY. ADR-003 reserves `schema_upgrade_path_unknown` for a v10 layout that is not the released shape, and limits automatic sources to 12, 13, and 14. The layout check that distinguishes those v10 shapes runs only inside `BundleUpgradeCoordinator._inspect`, which this inventory path does not call for versions below 12. `run_migrations` itself can apply a contiguous registry from any older `user_version`; startup will not.
- **Failure scenario:** A released pre-v12 ledger (well-formed v9, v10, or v11) stays on disk, unmigrated and without a backup, while diagnostics use the same reason as an ambiguous dev v10. An operator following the unknown-path guidance treats a simply unsupported schema like an unidentifiable one. A current route on the same catalog can still become READY, so the old task looks like a degraded startup rather than a refused migration with a named source version.
- **Suggested fix and test:** Keep the ADR boundary: do not auto-migrate below 12. In `_record_unsupported_bundle_route`, record `migration_unsupported` and the integer `schema_version` for every well-formed `user_version` below 12. Call `_validate_v10_bundle_layout` only when `user_version == 10`, and use `schema_upgrade_path_unknown` only when that helper raises. Test a v9 file and a released-shape v10 file: neither file’s bytes change, the v9 diagnostic is `migration_unsupported`, and only a v10 missing `content_capture_profiles_json` is `schema_upgrade_path_unknown`.
- **Confidence:** high
- **Related tests:** `tests/integration/service/test_bundle_upgrade.py` (dev v10 fail-closed when the coordinator inspects it); `tests/integration/storage/test_migration_0011_observation.py` (`test_legacy_dev_v10_fails_closed_before_running_released_migrations`)

## Examined and not filed

- **No `shell=True`.** Script subprocesses use argv lists. `support/npm-launcher/bin/yoetz.js` sets `shell: false`.
- **Rebuild migrations are necessary.** `0009` (observation cursors/events CHECK), `0014` (events summary CHECK; foreign keys dropped only around that rebuild, then `PRAGMA foreign_key_check` in `_verify_identity`), and catalog `0004`’s `start_operations` copy cannot be `ALTER`s. `0007` copies p1 query rows into p2 and keeps p1, as its header says. `0013` and `0010` use `ADD COLUMN`. On SQLite 3.45.1 the `0013` cross-column `CHECK` is stored and enforced for new writes; legacy rows stay all-NULL, which the `CHECK` allows.
- **`_requires_foreign_keys_disabled` turns foreign keys off for every pending migration in a batch that includes `0014`,** so a 12→15 upgrade also runs `0013` and `0015` with enforcement off. `0015` only creates an empty table; enforcement is restored in `finally` and `foreign_key_check` runs after commit. Bundle post-commit failure quarantines. No current SQL was shown to insert a dangling key, so this stays a latch for a later migration, not a present defect.
- **`tarfile.extractall(..., filter="data")`** in `provision_test_instance.py` is the data filter, not the legacy extractor.
- **`scripts/sync_mcp_descriptor_digests.py` `exec`s** a local AST of `src/yoetz/mcp/descriptors.py` after removing the `_lint_descriptor_sets()` call, then replaces digest strings. The path is the checkout, not user input.
- **`scripts/sync_committed_agent_trees.py`** refuses symlinks and only writes under the checkout’s `.agents/` trees.
- **`pyproject.toml`** pins runtime dependencies with `==`, pins `uv==0.11.29`, and exposes one entry point: `yoetz = yoetz.cli.entry:main`. No setuptools `package-data` override was found; migration bytes ship because they live under `src/yoetz/resources/` and the ripple mirrors them.
- **npm package** publishes only `bin/yoetz.js` and `README.md`, with no install scripts (`tests/packaging/test_npm_launcher.py`).

## Coverage

Service code was read only to see how the SQL files are applied. `tests/packaging/` was not executed. No live Yoetz service was started.


---

# Cross-cutting concurrency and errors

Source notes: `19-cross-cutting.md`.

# Cross-cutting audit (read-only)

Scope: `src/yoetz` Python, excluding generated mirrors. No services were started. No source was modified.

## Triage volume

Ripgrep over `src/yoetz` (`*.py`). `src/yoetz/resources` produced no hits for these patterns.

| Pattern | Raw hits |
| --- | ---: |
| `except Exception` | 458 |
| `threading.Lock` / `RLock` / `asyncio.Lock` | 74 |
| `subprocess.run` / `Popen` / `create_subprocess_exec` | 50 |
| `time.sleep` | 7 |
| bare `except:` | 0 |
| `shell=True`, `os.system`, `pickle`, `yaml.load`, `eval(`, `exec(` | 0 |
| `TODO` / `FIXME` / `XXX` / `HACK` | 1 (comment about TOML escapes in `codex_marketplace.py`, not a marker) |

The broad `threading|Lock|asyncio|Queue|wait(` search matches hundreds of files because `wait(` and `asyncio` are ordinary control-flow. Those were narrowed to lock objects, nested acquire order, and `asyncio.Lock.locked()` check-then-act.

Sites read in full enough to judge: every `time.sleep`; every `except Exception: pass` on a durability, vault, audit, or process-lifetime path; dispatch-gate acquire/release in `service/daemon.py`; capture-lock ownership in `observation_coordinator.py`; approved-check and git subprocess argv; privacy audit transitions behind the egress swallows; `observability/logging.py`, `privacy.py`, `diagnostics.py`, and `semantic_context.py`.

Most `except Exception` sites are fail-closed (return a typed denial, re-raise after rollback, or record a structural diagnostic and stop that lane). Those are not listed. Eight confirmed defects follow.

Observability: the structured logger deletes `msg`, `args`, and `exc_info` before emit, allowlists fields, and `redact_diagnostic_value` accepts only closed tokens, validated ids, hashes, bounded integers, or booleans. Unknown names are dropped. No log-injection or secret-logging defect was confirmed in `src/yoetz/observability/`.

Unsafe deserialization and shell invocation: none found. Subprocess call sites that were read use `shell=False` and a fixed argv prefix (`git_subject_state.py` `_GIT_SAFE_PREFIX`, approved checks via `CheckSandbox.prepare`, Codex app-server `create_subprocess_exec`).

Bounded `time.sleep` polls in `startup_gate.py`, `instance.py`, `release_runtime.py`, `observation_local.py`, and `approved_checks.py` wait on `flock` or process-group drain with a deadline, then fail or stop. They are not used as a stand-in for a condition variable. The Windows lock below is the exception.

## Findings

### X-01

- Severity: high
- Category: correctness
- Title: Approved-check `OSError` returns failure and leaves the child running
- File: `src/yoetz/adapters/approved_checks.py:650-680`
- Evidence: `TimeoutError` calls `_reap_approved_check_process`. The following `except OSError` returns `ApprovedCheckStatus.REJECTED` / `EXEC_FAILED` and does not reap. `finally` only closes the pipes and deletes the temp directory. `_bounded_communicate` reaches `os.read` and `process.wait` only after `Popen` has stored the child (`581-618`).
- Failure scenario: A launched check hits `OSError` on its stdout pipe (`EIO`, a closed fd, or `wait` after the pid becomes unwaitable). The runner reports `EXEC_FAILED` and drops the process group. With `start_new_session=True` the child is not in the service session, so closing the pipes does not kill it. The approved argv keeps running past `approval.timeout_seconds`, and any descendants the reaper would have killed on the timeout path stay alive for the life of the service (this process is also a child subreaper).
- Suggested fix and test: In the `OSError` handler, if `process is not None`, call the same `_reap_approved_check_process(process, process_group_id=process_group_id)` used by the timeout path before building the result. Keep the `EXEC_FAILED` status. Add a unit test next to `test_closed_output_pipes_still_time_out_and_reap_group` that patches `os.read` to raise `OSError` after `Popen` and asserts `killpg`/`process.kill` ran and `poll()` is not `None`.
- Confidence: high
- Related tests: `tests/unit/adapters/test_approved_checks.py` covers timeout and completed-group drain. It does not cover `OSError` after a live `Popen`.

### X-02

- Severity: high
- Category: security
- Title: Local semantic disclosure returns success when the audit completion write fails
- File: `src/yoetz/application/egress.py:1416-1455`; spend happens in `src/yoetz/adapters/privacy/gateway.py:1072-1079` and `src/yoetz/adapters/privacy/catalog.py:2299-2339`
- Evidence: `dispatch_local_semantic` calls `consume_local`, which commits `state = 'local_disclosure_pending'`, then runs `evaluator.evaluate`. Back in `_dispatch_approved`, `complete_local_disclosure` (the transition to `local_disclosure_completed`) is wrapped in `except Exception: pass`. `_map_provider_result` then returns `SemanticEgressSuccess` for `SemanticResultSuccess` even when the follow-up `audit.load` also fails (`1554-1570`).
- Failure scenario: The local model has already received `proposal.prepared_bytes`. A full disk, lock error, or state conflict on the completion update is discarded. The caller applies a successful AI review with `privacy_receipt_id=None`. The audit row stays `local_disclosure_pending`. A later `recover_started_request` treats that status as terminally unknown (`egress.py:500-510`), so the durable record contradicts the success already returned. Resume of `local_disclosure_pending` is not in the `reserved`/`approved`/`authorized` set (`906-911`), so it becomes `AUDIT_FAILED` rather than a second send; the defect is a missing receipt and a split outcome, not an automatic redispatch.
- Suggested fix and test: If `complete_local_disclosure` raises, do not call `_map_provider_result` with the provider success. Return `SemanticEgressBlocked` with `PrivacyOutcome.AUDIT_FAILED` / `PrivacyReason.OUTCOME_UNKNOWN` and the proposal id, and leave reconciliation to mark the pending row unknown. Log through `record_unexpected_exception_without_raising`. Test in `tests/unit/application/test_semantic_local_egress.py`: a fake audit whose `complete_local_disclosure` raises after `consume_local` must not produce `SemanticEgressSuccess`.
- Confidence: high
- Related tests: `tests/unit/application/test_semantic_local_egress.py`, `tests/integration/privacy/test_egress_gateway.py` (the gateway fake asserts `complete_local_disclosure` is not called from the gateway itself, which is why the coordinator write is the only receipt).

### X-03

- Severity: high
- Category: security
- Title: An unreadable egress audit row is reported as an authorization that was never spent
- File: `src/yoetz/application/egress.py:486-559`; caller `src/yoetz/service/ready_composition.py:5244-5260`; advice worker `src/yoetz/application/observation_advice_semantic.py:354-384`
- Evidence: `_load_started_disclosure_attempt` catches every `Exception`, logs it, and returns `None`. The comment says an unreadable row is not a spent authorization. `recover_started_request` returns `None` when that loader returns `None`, and its docstring says `None` means no reservation reached the consume CAS. `_reconcile_cancelled_observation_advice_semantic` returns `None` in that case, which the worker records as a plain `cancelled` outcome with no `outcome_unknown` receipt (`384`).
- Failure scenario: A foreground rebind cancels an advisory provider call after `consume` / `park_attempt_reconciliation` has committed. The audit read then fails (`SQLITE_BUSY`, a locked catalog, a transient I/O error). Recovery reports “never consumed.” The advice row is completed as an ordinary cancellation. The spent disclosure stays non-terminal until some later `reconcile_started_attempts` succeeds. `reconcile_started_attempts` itself returns `0` on any exception (`532-540`), so a persistent read failure never terminalizes the row. Operators and later advice see a clean cancel for a call that already left the machine.
- Suggested fix and test: Distinguish “no row” from “read failed.” On read failure, raise a typed audit error or return a dedicated unknown result; do not return `None`. The cancellation reconciler should keep the advice attempt non-terminal or stamp `failure_reason` to a closed “reconciliation unavailable” token instead of plain `cancelled`. Add a case in `tests/unit/privacy/test_catalog_audit.py` beside the existing `recover_started_request` cancel test where `load_started_disclosure_attempt` raises and the worker does not complete the row as a clean cancel.
- Confidence: high
- Related tests: `tests/unit/privacy/test_catalog_audit.py` around the cancel-window recovery (`1257-1264`) covers the happy parked-receipt path only.

### X-04

- Severity: medium
- Category: security
- Title: A failed human-denial write leaves the proposal approvable
- File: `src/yoetz/application/egress.py:1317-1329`; transition `src/yoetz/adapters/privacy/catalog.py:2661-2721`
- Evidence: On a non-approval `HumanPrivacyDecision`, `record_human_decision` is what moves `awaiting_human`/`reserved` to `decision_receipt_pending`. That call sits in `except Exception: pass`. Control then always continues into `_complete_semantic_predispatch` with `HUMAN_DENIED`. `record_human_decision` is also the only writer of `decision_structural_canonical` for the denial. `authorize` is only reachable from states that resume treats as `reserved`/`approved`/`authorized` (`906`). A row left in `awaiting_human` is still a legal input to a later `record_human_decision(..., approved=True)`.
- Failure scenario: The human denies. The denial `UPDATE` raises. This caller may surface `AUDIT_FAILED` if the follow-up `reserve` conflicts, or a denial block if that second write happens to land. Either way the original proposal can remain `awaiting_human`. A later approval of the same proposal id takes the `approved` branch and `authorize` can mint an egress authorization for bytes the human already refused.
- Suggested fix and test: Do not swallow `record_human_decision`. If it fails, return `SemanticEgressBlocked(AUDIT_FAILED)` and do not call `_complete_semantic_predispatch` until the denial state is durable. Retry must use the same proposal id and must not offer a fresh approval while the prior decision is unknown. Test with the catalog audit: deny, force `record_human_decision` to raise, assert the row is not `approved` afterward and `authorize` still rejects it.
- Confidence: medium
- Related tests: `tests/unit/privacy/test_catalog_audit.py` covers the state machine. No test asserts the coordinator’s `pass` around a failing denial write.

### X-05

- Severity: medium
- Category: correctness
- Title: Corrupt observation rows are omitted and the session still looks complete
- File: `src/yoetz/adapters/sqlite/observation.py:2258-2274`; consumers `src/yoetz/application/observation_advice.py:231-251` and `src/yoetz/application/semantic_content.py:635-668`
- Evidence: `_envelopes_from_rows` `continue`s on any `Exception` from `observation_envelope_from_json`, and also skips non-bytes blobs and non-object JSON. `list_envelopes_for_session` returns that shortened tuple. `scoped_session_envelopes` accepts any tuple as the full mapped session. `_session_envelopes` compares `len(envelopes)` to `len(loaded_items)` after the bad rows are already gone, so it does not add `CONTENT_CAPTURE_UNAVAILABLE`. Session status is computed from SQL coverage columns (`2100-2199`), not from this parse, so status can stay `ACTIVE`.
- Failure scenario: One `observation_events.structural_json` value fails the envelope parser (partial write, version skew, manual repair). Advice and captured semantic input are built from the remaining envelopes with no gap code. A check can report a clean observation history while that event is absent. The dropped event can be the one that carried a coverage gap or a tool-use boundary.
- Suggested fix and test: Count parse failures inside `_envelopes_from_rows` and surface them. `list_envelopes_for_session` should raise a typed storage error, or return a side channel the advice builder already understands (`ObservationGapCode`). `scoped_session_envelopes` and `_session_envelopes` must add a gap when the parsed count is short of the SQL row count. Test in `tests/unit/adapters/test_sqlite_observation.py`: insert one valid row and one invalid `structural_json`, assert the list call does not look like a complete one-envelope session.
- Confidence: high
- Related tests: `tests/unit/adapters/test_sqlite_observation.py` (`list_envelopes_for_session` limit and session filter). `tests/unit/application/test_observation_advice.py` stubs the lister and never feeds a corrupt row.

### X-06

- Severity: medium
- Category: correctness
- Title: A failed bundle close is marked done and skips the rest of the close
- File: `src/yoetz/adapters/runtime.py:1317-1334`; body `src/yoetz/service/ready_composition.py:2363-2385`
- Evidence: `_close_entry` sets `entry.closed = True` and clears rebind callbacks before `close_entry` runs. Any `Exception` returns immediately. `close_entry` closes the importer writer, then object db, then ledger db, then `_clear_active_fence`, in that order, with no per-step isolation. Callers (`_adopt_finished_opening`, rebind failure, idle eviction) have already dropped the entry from `_entries`.
- Failure scenario: `writer.close()` raises, or `_close_db` raises before the fence clear. The runtime will not try again because `entry.closed` is set. The ledger connection and the ownership fence can both remain. A later `route` for the same task opens a second writer against a bundle whose previous writer thread is still in `thread.join` or still holding the file. Two writers on one bundle is the split-brain the writer thread was built to prevent.
- Suggested fix and test: Close each resource in its own try and always attempt the rest, including the fence clear. Set `entry.closed` only after every step has been attempted; on failure poison the task id so a new open is refused until process restart rather than opening a second writer. Test with a factories double whose first `close` raises and assert the ledger close and fence clear still run, and that `route` does not open a second entry.
- Confidence: medium
- Related tests: runtime cache tests exercise poison and rebind. None assert close ordering after a raised `writer.close()`.

### X-07

- Severity: medium
- Category: performance
- Title: Capture recovery’s “don’t queue” check races the capture lock
- File: `src/yoetz/application/observation_coordinator.py:1087-1088` and `1117-1121`; budget `src/yoetz/application/observation_drain.py:293-305`
- Evidence: `recover_capture_inventory` returns `BUSY` when `self._capture_lock.locked()` is true, twice, specifically so a catalog scan does not queue behind a capture. `asyncio.Lock.locked()` is only a snapshot. The sweep calls this outside the ingest gate (`observation_drain.py:237-238`) and capture-only ingest holds `_capture_lock` without that gate (`observation_coordinator.py:2050`, `daemon.py:1131-1135`). The drain advances `_capture_recovery_after` before the await (`297`).
- Failure scenario: The sweep observes the lock free, a native capture acquires it, then recovery blocks in `async with self._capture_lock` until the capture finishes or `asyncio.timeout` fires. The lane is reported `TIMEOUT` and the cursor moves past that workspace. A long capture burns the 5s recovery budget and delays the four-lane turn; the workspace that was actually busy is not retried until the cursor wraps.
- Suggested fix and test: Try `await asyncio.wait_for(self._capture_lock.acquire(), 0)` (or a zero timeout) and on `TimeoutError` return `BUSY` without waiting. Do not advance `_capture_recovery_after` when the result is `BUSY`. Test two tasks: one holds `_capture_lock`, `recover_capture_inventory` returns `BUSY` in well under the recovery budget.
- Confidence: high
- Related tests: `tests/unit/adapters/test_observation_store_lock.py` covers the cross-process flock timeout, not this asyncio lock race.

### X-08

- Severity: medium
- Category: correctness
- Title: Windows pending-lock acquire retries every `OSError` forever
- File: `src/yoetz/service/elevated_bootstrap.py:631-645`
- Evidence: `_acquire_windows` loops `msvcrt.locking(..., LK_NBLCK, 1)` and on any `OSError` sleeps 50ms and retries. There is no deadline and no errno filter. The POSIX path in `__enter__` uses a blocking `flock` and raises `ElevatedBootstrapError` on `OSError` (`606-615`).
- Failure scenario: On a native Windows interpreter, a permanent error (bad descriptor, permission, lock region) is treated as contention. The elevated bootstrap thread sleeps forever and never raises `pending_lock_failed`. A human confirmation that needs this lock never finishes.
- Suggested fix and test: Match the POSIX failure policy: retry only the documented contention error, bound the wait, then raise `ElevatedBootstrapError("pending_lock_failed")`. Test with a fake `msvcrt.locking` that raises `OSError` and assert the acquire raises within a fixed number of attempts.
- Confidence: high
- Related tests: none found for `_acquire_windows`. This path runs only when `fcntl` is absent and `msvcrt` is present. Supported Windows use is WSL2, which takes the POSIX path.

### X-09

- Severity: medium
- Category: performance
- Title: SQLite writer `close()` joins on the caller and can block the service loop
- File: `src/yoetz/adapters/sqlite/connection.py:694-700`; invoked from async `src/yoetz/service/ready_composition.py:2370-2373` via `src/yoetz/adapters/runtime.py:1323-1330`
- Evidence: `SqliteWriterThread.close` sets `_accepting = False`, then `self._queue.put(_CLOSE_SENTINEL)` (blocking, queue max size is `WRITER_QUEUE_DEPTH`), then `self._thread.join()` with no timeout. `close_entry` is an async factory called from the ready runtime on the service loop. The writer loop is the only consumer that can free a full queue.
- Failure scenario: Idle eviction or service shutdown closes a hot bundle while the writer queue is full or a job is still inside SQLite. `join` runs on the event loop, so control accepts, observation sweeps, and vault idle handling stop until that queue drains. If the writer thread has already exited and the queue is still full, `put` waits forever and the loop never reaches `join`.
- Suggested fix and test: Put the sentinel with a timeout; if the queue is full, reject new work (already done by `_accepting`) and have the writer notice a close flag without needing a free slot. Run `join` in a worker thread with a deadline, and fail the close as a typed error if the thread is still alive. Test a full queue plus a live writer: `close()` must return without calling a blocking `join` on the loop thread.
- Confidence: medium
- Related tests: sqlite connection tests cover busy submission. They do not assert that `close()` stays off the loop.

## Not confirmed

- No deadlock was confirmed in the ready dispatch order (observation gate, then maintenance gate, reverse release in `daemon.py:1152-1160`) or in `LocalBundleRuntime`’s `asyncio.Condition(self._lock)` wait.
- `asyncio.wait_for(gate.acquire(), ...)` in `daemon.py:334-343` was checked against CPython 3.12.3 `Lock.acquire`. A timeout cancellation releases the wait and does not leave `_locked` set. Not reported.
- Fresh `asyncio.Lock()` objects passed into short-lived `MemoryLedgerAdapter` oracles are uncontended clones under the repository lock. Not a cross-call race.
- `except Exception: pass` around vault `lock()` during unlock failure (`daemon.py:847-855`) is best-effort cleanup before re-raising. The public error still propagates.
- Project coordination and check-advice `except Exception` paths that return `False`, `()`, or skip one project are fail-closed for disclosure and do not change the deterministic check verdict.


---

# Performance

Source notes: `20-performance.md`.

# Yoetz performance audit (src/yoetz, excluding resources/)

Read-only review of request-path latency and memory. Scope is algorithmic and I/O hotspots that grow with ledger length, worktree size, or schema size. Bounded importers (4 MiB / 20_000 lines) and observation-event retention (256 rows) were inspected and are not listed. No material `O(n^2)` string concatenation showed up; the quadratic cost is repeated immutable map copies inside ledger replay.

## Exec summary

Ordinary publish, check, respond, and receipt work replays the whole task ledger from genesis, and each folded event copies projection maps that grow with the chain. One append on a ledger of `n` events is about `O(n^2)` CPU and allocation, and it holds the task lock while that runs. Receipt and historical status repeat that replay several more times, then decrypt every live payload to decide availability. After restart, recovery decrypts every event payload into a process-lifetime tuple before the first RPC returns. Check phase changes rewrite every historical `operations.result_canonical` blob. Approved-check verification multiplies a double Git snapshot that can buffer up to 64 MiB of diff and walk 10_000 files, several times per job. MCP startup compiles every tool schema, and every public RPC builds a fresh JSON Schema validator.

## Findings

### Z-01

- **Severity:** high
- **Category:** performance
- **Title:** Non-observation appends replay the ledger from genesis under the task lock
- **File:** `src/yoetz/adapters/memory/ledger.py:1683-1697`; `src/yoetz/kernel/reducers.py:358-366`, `835-908`, `935-1024`, `1059-1079`, `1304-1323`, `1363-1370`, `1470-1486`
- **Evidence:** `append_batch` keeps `async with self._lock` across replay. Observation batches call `replay_extension_with_index`, which still runs `build_replay_index(prior_records)` over the whole prefix. Every other batch calls `replay_with_index(proposed)`, which folds every prior event. `extend_replay_index` copies the payload, evidence, and redaction maps. `reduce_event` copies every projection collection, then `_recompute_secondary_effects` sorts plans, decisions, and claims and `_recompute_missing_gaps` walks every ref. `_run_blocking_joined` moves the CPU off the event loop and leaves the task lock held.
- **Slow scenario:** A publish, check, respond, or receipt of one event on a ledger of `n` events costs about `O(n^2)` time and temporary memory, because step `i` copies maps of size `i`. Growing a task one event at a time costs about `O(n^3)` over its life. Other RPCs for that task wait on the same lock. Observation appends are the cheaper branch: one full index build plus one reduce, about `O(n)` per hook-driven event, `O(n^2)` over the task life.
- **Suggested change:** Keep the `ReplayIndex` and projection that recovery and the last successful append already proved, and fold only the new records. Make secondary effects incremental (supersession and contradiction maps keyed by id) so a new event updates the touched rows. Release the task lock around the pure fold, or fold before taking the lock and re-check the head digest before commit. Add a regression that a one-event append performs work proportional to the batch, not to `len(records)`.
- **How to test:** Unit-time a memory ledger from 100 to 5_000 single-event publishes and plot CPU per append. Assert the observation branch does not call `replay_with_index`. Confirm a concurrent status on the same task is not blocked for the full fold once the lock scope shrinks.
- **Confidence:** high
- **Related tests:** `tests/integration/storage/test_append_and_replay.py` locks replay correctness, not cost. `tests/unit/kernel/test_reducers_each_family.py` and `tests/unit/kernel/test_receipt_capacity.py` call `replay_with_index` on small fixtures. No scaling benchmark.

### Z-02

- **Severity:** high
- **Category:** performance
- **Title:** Receipt, respond, and candidate status replay the same prefix again after the ledger already did
- **File:** `src/yoetz/application/receipt.py:396-406`, `770-776`; `src/yoetz/application/respond.py:255-269`; `src/yoetz/application/status.py:893-911`; `src/yoetz/kernel/deterministic_checks.py:1488-1498`; `src/yoetz/adapters/memory/ledger.py:1526-1537`
- **Evidence:** `_records_through` materializes `load_events` and calls `replay`. `execute_receipt` calls `replay(records)` again, then `load_case_availability`, then `build_deterministic_case`. The public case builder replays the prefix once more unless `_projection_validated` is set, then `validate_replay_index` builds the reverse index again (`reducers.py:1453-1459`). When the requested projection is not the live head, `_projection_anchored_unlocked` replays the prefix and compares it for equality. Respond uses the same `_records_through` pattern. Status `view=candidate_findings` loads the prefix, replays, then builds a case the same way.
- **Slow scenario:** One receipt on `n` events pays the Z-01 append replay plus three or four more genesis replays and an equality compare of the whole `ProjectionState`. Candidate-findings status pays two or three. Latency tracks `O(n^2)` per call and peaks when the agent polls status while publishing.
- **Suggested change:** Thread the append-time `(projection, replay_index)` into receipt, respond, and status the way `build_deterministic_case` already allows via `_projection_validated` and `_replay_index`. Authenticate a historical frontier by comparing `head_digest` at that sequence, which the in-memory chain already stores, instead of replaying. Keep one genesis replay as the recovery and corruption path.
- **How to test:** Count `replay` calls with a spy during `execute_receipt` and candidate status on a 1_000-event ledger. Target is one replay per request when the head matches, and zero extra replays when the adapter projection is current.
- **Confidence:** high
- **Related tests:** Receipt and status unit tests cover conflict and corruption branches. None measure replay multiplicity.

### Z-03

- **Severity:** high
- **Category:** performance
- **Title:** Recovery decrypts every payload and keeps the whole chain resident
- **File:** `src/yoetz/adapters/sqlite/repository.py:553-676`, `730-757`; `src/yoetz/adapters/objects/encrypted_files.py:473-479`
- **Evidence:** `_recover_projection` `SELECT`s every event joined to its locator, `ORDER BY ingestion_seq`, `fetchall()`, then `_decode_durable_record` per row. For each present event it `open_verified`s the payload, `strict_json_parse`s it, `decode_payload`s it, and `canonical_encode`s the record to compare bytes. Artifact refs trigger more hydrates. The resulting tuple is stored on `MemoryLedgerState.records` for the life of the route. Replay itself is offloaded with `asyncio.to_thread`, but decryption and canonical checks run on the event loop (a `sleep(0)` every 32 rows).
- **Slow scenario:** First RPC after process start, or any new task route, blocks until every historical payload is decrypted. RSS grows with the sum of plaintext payloads, not with the projection. A task with thousands of evidence or observation events pays that cost once per service life and then keeps it.
- **Suggested change:** Persist the projection snapshot the reducer already digests (`projection_state.state_digest`) and recover by loading that snapshot plus a tail of events since `applied_through_seq`. Decrypt a payload when a caller needs it. Stream rows with a cursor instead of `fetchall()`. Move object opens off the event loop with the replay.
- **How to test:** Build a bundle of `n` small events and one bundle whose payloads are large, restart the service, and time the first status. Memory should stay near projection size when payloads are not on the status path.
- **Confidence:** high
- **Related tests:** Storage recovery tests assert byte identity after restart. No timing or RSS check.

### Z-04

- **Severity:** high
- **Category:** performance
- **Title:** Every check phase sync rewrites every historical operation blob
- **File:** `src/yoetz/adapters/sqlite/repository.py:1317-1516`, `2272-2359`
- **Evidence:** `_sync_runtime_state` loops `self._state.operations`, jobs, attempts, and disclosure waits. For each operation it `SELECT`s the row and `UPDATE`s it, including `result_canonical`, and sets `updated_at` to now. The same full rewrite runs on check phase advance, semantic enqueue, claim, attempt outcome, and disclosure changes (`_sync_after_mutation` callers from line 2283). `result_canonical` is the stored public result blob (`migrations/bundle/0001.sql:44`).
- **Slow scenario:** One semantic check is several phase transitions. Each transition writes `O(operations)` rows and rewrites every past check and receipt blob. A task with hundreds of checks rewrites megabytes of unchanged BLOB pages per phase. The write sits inside the task lock and the SQLite `BEGIN IMMEDIATE` in `_sync_after_mutation_locked`.
- **Suggested change:** Dirty-track the operation, job, and attempt mutated by the transition and upsert only those keys. Stop bumping `updated_at` when the durable columns are unchanged. Keep `result_canonical` write-once at completion.
- **How to test:** Count `UPDATE operations` statements during one check on a ledger that already has 200 complete operations. The count should be the new row plus any row whose lease actually changed.
- **Confidence:** high
- **Related tests:** SQLite repository tests cover commit and rollback. They do not count writes.

### Z-05

- **Severity:** high
- **Category:** performance
- **Title:** Project advisory check full-replays every counterpart task ledger
- **File:** `src/yoetz/application/check.py:471-523`, `604-638`, `864-871`, `2070`, `2338`
- **Evidence:** After a check result is frozen, `_attach_current_project_advisory_notes` calls `_duplicate_project_advisory_task_ids`. For each admitted counterpart it routes that task and `_current_task_findings` does `tuple(async for record in load_events(...))` then `replay(records)` to read unresolved findings. The requester's own projection is not reused.
- **Slow scenario:** A project with `m` member tasks of `n` events each adds about `m * O(n^2)` replay to every check, including replayed checks (`check.py:2070`). Large projects make `check` slower than the caller's own ledger.
- **Suggested change:** Read `projection.findings` from the counterpart route's already-resident projection (`load_projection` of the findings view, or a narrow port that returns unresolved finding identities). Do this only when the caller asked for project advice, and cap the member scan with the existing admission list rather than opening ledgers that fail the structural-context check first... the context check already runs first; keep the finding read on the projection.
- **How to test:** Spy `replay` during a check in a 10-member project. Advisory overlap should not call `replay` when each route's projection is current.
- **Confidence:** high
- **Related tests:** Check tests cover advisory notes and admission refusal. No multi-ledger cost test.

### Z-06

- **Severity:** high
- **Category:** performance
- **Title:** Approved-check verification repeats a double Git snapshot of the worktree
- **File:** `src/yoetz/adapters/git_subject_state.py:349-469`, `565-633`, `635-693`; `src/yoetz/ports/subject_state.py:26-27`; `src/yoetz/application/observation_coordinator.py:4783-4838`; `src/yoetz/application/observation_verification.py:392-403`, `463-485`
- **Evidence:** `GitSubjectStateAdapter.capture` snapshots identity, captures components, then captures again to prove stability. Each `_capture_components` runs `git status --porcelain=v2 --untracked-files=all`, `git diff --cached`, `git diff` (stdout capped at `MAX_SUBJECT_STATE_HASH_BYTES + 1` = 64 MiB + 1), hashes every untracked file, and `_reject_unsafe_tree_entries` `scandir`s the tree up to `MAX_SUBJECT_STATE_FILES` (10_000). Git timeout is 10 seconds per command. `_prepare_verification_worker` calls `capture` once to digest the tree before enqueue. `run_bound_approved_check` calls it before and after the approved command, and `run_once` calls it again after the thread returns.
- **Slow scenario:** One approved check on a dirty repo is on the order of 4 to 6 full captures, each two tree walks and two diffs. A 10_000-file tree with a large unstaged diff holds two ~64 MiB buffers and can burn the 10 second Git timeout per command. That work is synchronous on the verification path (`asyncio.to_thread` still occupies a worker for the whole capture). Enqueue already pays one double capture before the job is queued.
- **Suggested change:** Hash a stable identity (HEAD, index checksum, untracked path list) once, and run the second capture only when the first identity moved. Share one capture between enqueue, pre-check, and post-check when the digest matches. Stream `git diff` into a hasher with the existing byte cap instead of retaining both diffs. Skip `--untracked-files=all` content hashing when the path inventory digest is unchanged.
- **How to test:** Count `git` subprocesses and bytes retained while verifying one approval in a fixture repo of a few thousand files. A clean unchanged tree should be a small constant number of Git calls, not six full snapshots.
- **Confidence:** high
- **Related tests:** Git subject-state unit tests cover limits and refusal. They use small fixtures.

### Z-07

- **Severity:** medium
- **Category:** performance
- **Title:** Every public RPC compiles a new JSON Schema validator, often three times
- **File:** `src/yoetz/protocol/schemas.py:906-920`; `src/yoetz/protocol/models.py:1406-1425`, `4066-4089`; `src/yoetz/mcp/server.py:1942`, `2059-2060`; `src/yoetz/service/control_protocol.py:391-406`
- **Evidence:** `validate_schema_instance` constructs a new `Draft202012Validator` on every call. Pydantic request models call `_validate_model_against_schema` in their after-validators. `public_model_to_wire` validates the dumped result again. The MCP bridge then `model_validate`s that wire, which validates a third time. The control channel wraps the same body in `_validated_wire` for `control-request` and `control-result`. Catalog load is cached (`_load_catalog_state`, `lru_cache`); validator compilation is not. Status and check result schemas are large `oneOf` documents.
- **Slow scenario:** Cost is per RPC and grows with schema size, not with `n`. A status poll still pays multiple full Draft 2020-12 compiles of `status-result` on the bridge and again inside the service. That dominates when the ledger is small and remains a fixed tax when the ledger is large.
- **Suggested change:** Cache one compiled validator per `(schema_id)` for the process. On the MCP success path, validate the result once and reuse that wire for the control frame. Keep the Pydantic model as the structural parser and the cached schema validator as the wire check.
- **How to test:** Microbench `public_model_to_wire` on a representative status result before and after a validator cache. Trace one MCP `status` and count `Draft202012Validator` constructions; target is one per distinct schema per process, plus one instance walk per body.
- **Confidence:** high
- **Related tests:** Protocol schema conformance tests check rejection paths. `#210` already removed catalog meta-validation from handshake; instance compilation was left per call.

### Z-08

- **Severity:** medium
- **Category:** performance
- **Title:** MCP serve pays the full CLI import plus eager schema bundling before the first byte
- **File:** `src/yoetz/cli/entry.py:1-6`; `src/yoetz/cli/app.py:16-86`, `2000-2007`; `src/yoetz/mcp/server.py:19-109`, `439-443`; `src/yoetz/mcp/descriptors.py:1325-1355`, `1419-1458`, `1600-1610`, `2083`
- **Evidence:** Hook commands fast-path in `entry.py` specifically because importing `yoetz.cli.app` loads Typer, Pydantic, and the protocol schema stack (`#242`). `yoetz mcp serve` is not on that fast path: it imports `cli.app`, then `yoetz.mcp.server`, which imports `mcp`, `protocol.models`, and `protocol.schemas`. `build_bridge_runtime` touches every descriptor's `input_schema` and `output_schema` before serving. Those properties call `_mcp_presentation_schema` / `_mcp_output_presentation_schema`, which load the catalog and rewrite external `$ref`s into one bundle per tool. `_lint_descriptor_sets()` runs at descriptor import.
- **Slow scenario:** Every host MCP process start (Cursor, Claude Code, Codex) pays interpreter startup plus this import and seven or so schema bundles before `initialize` returns. The bundles are cached after that. Cold start is the user-visible stall; it does not grow with the ledger.
- **Suggested change:** Give `mcp serve` the same entry fast path hooks already have, so Typer is not required to reach the bridge. Build presentation schemas once at package time and ship the bundled JSON, or compute them on first `tools/list` rather than in `build_bridge_runtime` for schemas the host has not listed. Keep digest checks on the shipped bytes.
- **How to test:** `python -X importtime -c` the serve path versus `hooks observe`, and time `build_bridge_runtime` alone. A fast path should not import `typer`.
- **Confidence:** high
- **Related tests:** Hook import-budget coverage lives around `#242`. No MCP cold-start budget test was found.

### Z-09

- **Severity:** medium
- **Category:** performance
- **Title:** Status builds every view row, then filters; a historical frontier replays the prefix
- **File:** `src/yoetz/adapters/memory/ledger.py:1933-2006`, `2077-2080`; `src/yoetz/application/status.py:812-830`; `src/yoetz/kernel/projections.py` query tables are unused at runtime (`migrations/bundle/0001.sql:955-1720` defines `p1_query_*` indexes; runtime `SELECT`s of those tables were not found outside migration and upgrade name lists)
- **Evidence:** `query_projection` copies the full record tuple. If the requested frontier is not the cached head, `_query_snapshot` builds `prefix` with a linear scan and calls `replay(prefix)`. `_projection_items` materializes the whole view (every history row, every obligation, every finding). Filters and the page limit apply afterward, breaking only after `limit + 1` survivors, so an unfiltered history page still builds every row. A non-head status also walks `load_events` in `_exact_frontier` to recover the digest. The durable `p1_query_*` indexes are not read on this path.
- **Slow scenario:** `status view=history` on an `n`-event task allocates `n` models per page. Paging with `after_sequence` still starts from a full item tuple unless the cache hits (cache holds 8 keys and drops on any new record). Asking for an older frontier adds a genesis replay, about `O(n^2)` from Z-01.
- **Suggested change:** Slice history by ingestion sequence before building models (`records[offset:offset+limit]`). For other views, filter projection maps by the indexed fields already stored on the records, and stop at `limit`. Serve historical frontiers from a retained projection checkpoint or from the tail fold. Either read the `p1_query_*` tables or stop maintaining them if they are only migration residue.
- **How to test:** Time `view=history` with `limit=20` at `n` of 100 and 5_000. Allocations of `StatusHistoryItemModel` should be about 20, not `n`.
- **Confidence:** high
- **Related tests:** Status paging tests check cursors and filters on small ledgers.

### Z-10

- **Severity:** medium
- **Category:** performance
- **Title:** Case availability decrypts every live payload and captured object
- **File:** `src/yoetz/adapters/memory/ledger.py:1846-1931`; `src/yoetz/adapters/sqlite/repository.py:2087-2122`
- **Evidence:** `load_case_availability` walks every non-redacted plan, obligation, decision, assignment, action, result, evidence, claim, finding, and response, then `open_verified`s each payload object until EOF. It does the same for every evidence row with a captured object. SQLite `_refresh_native_capture_refs` adds a per-evidence manifest query before that. Receipt and candidate status both call this (`receipt.py:772`, `status.py:911`).
- **Slow scenario:** A case with `e` evidence objects of size `b` reads and authenticates about `O(e * b)` ciphertext on every receipt and candidate status, even when the objects have not changed since the previous call. Large captured files dominate.
- **Suggested change:** Cache availability by `(object_id, envelope_digest)` for the process, and invalidate on redaction or sweep. Stat the envelope and compare the stored commitment before decrypting. Batch the manifest lookup with one `IN` query.
- **How to test:** Two receipts at the same frontier should decrypt each object once. A unit spy on `open_verified` makes that visible.
- **Confidence:** high
- **Related tests:** Availability tests cover missing and redacted objects. They do not count reads.

### Z-11

- **Severity:** medium
- **Category:** performance
- **Title:** Recovery loads every complete operation blob and queries event ids per operation
- **File:** `src/yoetz/adapters/sqlite/repository.py:861-906`; `migrations/bundle/0001.sql:23-51`
- **Evidence:** After the event scan, recovery `SELECT`s every `operations` row with `state='complete'`, including `result_canonical`. There is no index on `state`; the primary key is `(writer_id, operation_id)`. For each row with a sequence range it runs `SELECT event_id FROM events WHERE ingestion_seq BETWEEN ? AND ? ORDER BY event_id`. `ingestion_seq` is the events primary key, so each query is an indexed range, and there is one query per historical operation. Pending operations add a resume-object decode each (`repository.py:779-860`).
- **Slow scenario:** Restart of a task with `p` completed operations and large result blobs reads all of those blobs and issues `p` extra queries before the route serves. Combined with Z-03, cold start scales with both events and operations.
- **Suggested change:** Store the structural event-id tuple on the operation row at commit time (it is already known in `_persist_append`). Select complete operations without `result_canonical` until a replay of that operation needs the blob. Add `operations(state)` only if the filtered scan stays hot after the column split.
- **How to test:** Restart a bundle with 500 tiny complete operations and count SQL statements in the recovery trace. Event-id lookups should be zero if the ids are stored on the row.
- **Confidence:** high
- **Related tests:** Recovery correctness tests. No query-count budget.

### Z-12

- **Severity:** medium
- **Category:** performance
- **Title:** Each hook reconcile canonical-encodes and rewrites the whole observation state file
- **File:** `src/yoetz/adapters/integrations/observation_local.py:147-170`, `500-510`, `8319-8345`, `8545-8576`; `src/yoetz/cli/observe_hooks.py:3735-3803`
- **Evidence:** `_read_bytes` rejects files above the cap only after `stat`, then `read_bytes()` the whole file. The legacy ceiling is 36 MiB and the expanded ceiling is 16 MiB (`_MAX_LEGACY_STATE_BYTES`, `_MAX_EXPANDED_STATE_BYTES`). `_load` JSON-parses that buffer and deep-copies it out of the stat cache. `_encode_state` runs `canonical_encode` on the entire workspace document. `_save` atomically replaces the file (`_atomic_write` fsyncs). Codex observe reconciles the session stream and calls `refresh_advice`, which loads and saves this document. The stat cache skips a re-read when the file is unchanged; a save still re-encodes everything.
- **Slow scenario:** A busy hook (every tool call) on a workspace whose state has grown toward the cap spends hook latency on a multi-megabyte canonical JSON encode, fsync, and rename. Hosts block on the hook. Memory spikes by the raw file plus the parsed tree plus the encoded bytes.
- **Suggested change:** Split hot cursor and dedup state from the advice snapshot so a hook updates a small record. Encode incrementally or cache the previous canonical bytes and patch the changed keys. Keep the 16 MiB hard cap.
- **How to test:** `scripts/benchmark_observation_hooks.py` against a state file at 1 MiB and near the cap. Per-hook CPU should stay flat if the hot record is small.
- **Confidence:** high
- **Related tests:** `scripts/benchmark_observation_hooks.py`, `scripts/benchmark_observation_selection.py`, `tests/unit/observability/test_observation_selection_benchmark.py`. They cover selection cost more than full-state encode.

### Z-13

- **Severity:** medium
- **Category:** performance
- **Title:** Obligations status rebuilds an event map per obligation
- **File:** `src/yoetz/adapters/memory/ledger.py:1068-1093`; `src/yoetz/kernel/command_attempts.py:11-106`; `src/yoetz/kernel/receipt_builder.py:1445-1452`
- **Evidence:** The obligations view calls `command_attempts(projection, records, obligation)` for every obligation with a payload. Each call builds `by_event` by scanning the full record tuple (`command_attempts.py:26-28`), then scans actions. Receipt text calls it again for up to 10 retained obligations. `closure_command_gaps` calls it for every completion-claim obligation (`command_attempts.py:109-134`).
- **Slow scenario:** `status view=obligations` with `o` open obligations and `n` events is about `O(o * n)` scans and dicts before paging. Agents are told to call this view before resolving work, so it sits on the publish path.
- **Suggested change:** Build `by_event` once per status request and pass it in. Index command attempts by obligation id when the action is reduced, and read that index here.
- **How to test:** Time the obligations view at `o=50`, `n=5_000`. A shared index should make the view linear in the returned page.
- **Confidence:** high
- **Related tests:** Command-attempt unit tests use a handful of events.

### Z-14

- **Severity:** medium
- **Category:** performance
- **Title:** Publish dry-run replays the live ledger plus the provisional batch
- **File:** `src/yoetz/application/publish_work.py:1340-1394`
- **Evidence:** Dry-run loads `existing_records`, builds provisional records, and calls `replay((*existing_records, *provisional))`. The comment on `_projection_at_result_frontier` (`publish_work.py:942-952`) says a successful append already avoids a second replay by reading the adapter projection. Dry-run does not use `replay_extension`.
- **Slow scenario:** Agents dry-run before every publish. Each dry-run is a full genesis replay, about `O(n^2)` from Z-01, and allocates a second record tuple. The write path then replays again inside `append_batch`.
- **Suggested change:** Fold the provisional batch with `replay_extension` off the resident projection, and discard the result. The live projection stays authoritative for the subsequent real append.
- **How to test:** Spy `replay` versus `replay_extension` on dry-run of one event against a 2_000-event ledger. Dry-run should fold one event.
- **Confidence:** high
- **Related tests:** Publish dry-run tests assert findings and conflict codes, not cost.

### Z-15

- **Severity:** medium
- **Category:** performance
- **Title:** Finding explanations replay the pre-check prefix once per distinct check
- **File:** `src/yoetz/kernel/finding_resolution.py:382-408`; `src/yoetz/adapters/memory/ledger.py:1149-1177`; `src/yoetz/kernel/receipt_builder.py:1455-1464`
- **Evidence:** `finding_resolution_explanation` calls `_historical_proof_state`, which filters `records` to the pre-check prefix and `replay`s it. The cache key is the candidate check sequence plus the check frontier, so findings that share a check reuse one replay. Status findings builds this explanation for every finding in rank order before the page limit is applied (the view items are fully built in `_projection_items`). Receipt text does it for up to 10 findings.
- **Slow scenario:** A findings page whose issues were produced by `c` different checks replays `c` prefixes, each `O(n^2)` in the worst prefix, on top of the status query's own item build. Long-lived tasks that re-check often pay this on every findings poll.
- **Suggested change:** Store the resolution witness on the finding projection when the later check is reduced, and render the explanation from that witness. If a replay must remain, key it only by candidate sequence and share it across the page.
- **How to test:** A findings page with 20 findings from 5 checks should replay at most 5 prefixes today and zero prefixes once the witness is stored. Assert the cache is shared for the page (it is, per call) and that item build stops at the page size.
- **Confidence:** medium
- **Related tests:** `tests/unit/kernel` finding-resolution cases on short chains.

## Checked and not listed

- Importers (`codex_jsonl.py`, `codex_rollout_jsonl.py`) cap source bytes, line bytes, and line count. Parsing is one pass.
- `observation_events` retention is 256 rows per workspace (`adapters/sqlite/observation.py:58`). The status query filters `(workspace_commitment, session_commitment) ORDER BY id` while the index is `(workspace_commitment, receipt_time)` (`migrations/bundle/0009.sql:71-72`). At 256 rows that scan is not a hotspot.
- Workspace inspect and Git path listing are byte-capped. The unbounded-feeling cost is the multiplied full capture in Z-06, not a missing cap.
- Provider calls use `httpx` on the semantic-review path, off the hook (`#619` is cited from the advice scheduler). No synchronous socket call was found on the ordinary publish path.
- String building in receipts, MCP summaries, and canonical JSON uses `join` or a bounded number of concatenations.

## Coverage

Highest-confidence costs are the in-memory ledger fold (Z-01 through Z-05) and Git verification (Z-06). Schema and MCP startup costs (Z-07, Z-08) are real and constant. No benchmark covers ledger replay scaling. Observation hook benchmarks exist and do not cover multi-megabyte state encodes.


---

# Test gaps

Source notes: `21-tests.md`.

# Test-quality audit: tests/ vs src/yoetz

Read-only. No suite run. No `tests/packaging` execution. Sampled `tests/unit/service`, `tests/unit/kernel`, `tests/conformance/privacy`, `tests/conformance/honesty`, `tests/integration/service`, `tests/unit/adapters`, plus ripgrep for `time.sleep`, `pytest.mark.skip`, `pytest.mark.xfail`, and `MagicMock`.

Tree size: 285 non-`__init__` modules under `src/yoetz`, 591 Python files under `tests/` (unit 339, integration 93, conformance 66, subprocess 32, packaging 29, capability 23). `MagicMock` does not appear under `tests/` or `src/yoetz`. `pytest.mark.xfail` appears only under `tests/packaging/`.

Checked and not filed: the SQLite writer authorizer does deny `ATTACH`, `load_extension`, and `writable_schema` in `tests/integration/storage/test_build_and_pragma_gate.py` (lines 188–195). Chat-user attestation has real negatives in `tests/unit/cli/test_elevated.py` (target mismatch, warning, forged schema). Installation-recovery tamper is tested in `tests/integration/objects/test_installation_recovery.py`; that does not cover the portable passphrase module below.

## Findings

### A-01

- Severity: high
- Category: quality
- Title: Control-socket symlink and extra-link rejection is untested
- File: `tests/integration/service/test_local_control_channel.py:120-124`, `159-186`; oracle in `src/yoetz/adapters/control/unix_socket.py:397-411`
- Evidence: The happy-path test asserts `st_nlink == 1` and owner-only mode on the three sockets. The fail-closed test replaces the endpoint with a regular file, then with mode `0o666`, then calls `authenticate_peer` with `os.geteuid() + 1`. Ripgrep shows no `symlink` in this file. `capability/test_local_control_channel.py:51-54` also only checks mode and uid. `_verify_endpoint` refuses a symlink (`S_ISLNK`) and any `st_nlink != 1` before connect.
- What bug could slip: Dropping the symlink or link-count checks still leaves both tests green. A planted symlink or hard link at `control.sock` / `secret.sock` / `human.sock` could be followed or aliased and the tests would not notice.
- Suggested test: Under the existing runtime-directory fixture, replace the bound socket path with a symlink to another owner-mode socket, and separately `os.link` the live socket so `st_nlink == 2`. Both `connect_control` and `connect_secret` must raise `LocalControlTransportError` with reason `endpoint_unsafe`, and neither path may exchange a payload. Repeat for a symlink runtime directory (`run` → another directory) and assert `runtime_directory_unsafe` from `_verify_runtime_directory`, which checks `S_ISLNK` at lines 387–393.
- Confidence: high

### A-02

- Severity: high
- Category: quality
- Title: Privacy conformance locks vocabulary, not the never-send or scope fixtures
- File: `tests/conformance/privacy/test_never_send_scope_and_channels.py:11-16`; `tests/conformance/privacy/test_privacy_profiles.py:12-34`
- Evidence: `test_never_send_fixture_and_domain_registry_are_identical` compares `fixtures/privacy/PRIV-005-never-send.case.json` policy `never_send` strings to `ForbiddenDataKind`. It never reads `expected.profile_matrix`, `canary_cases`, or `sink_canary_absence`. Profile tests only check that fixture strings sit in the `PrivacyProfile` enum. `PRIV-006-policy-loosening.case.json` and `PRIV-007-cross-scope.case.json` are named only in `tests/packaging/test_privacy_docs_and_resources.py:51-52`. `tests/unit/privacy/test_local_enforcer.py` asserts two kinds (`API_CREDENTIAL` at line 105, `UNRELATED_ENVIRONMENT` at line 171), not the sixteen fixture canaries, mixed encodings, or `scope_derivation_rejections` (including `symlink`) in PRIV-007.
- What bug could slip: A classifier that misses `cookie`, `private_certificate`, `raw_database`, or a split/base64 canary still matches the enum test. A scope check that allows parent-workspace or sibling-task reuse of a request grant still leaves conformance green. PRIV-006 widening (`local_only` to `minimal_external` must be `decision_required`; tightenings commit immediately) is not executed.
- Suggested test: Drive `LocalPrivacyEnforcer` (or the coordinator used in production) over each PRIV-005 `canary_cases[]` item and assert `outcome == blocked_forbidden_data`, `network_attempts == 0`, and the canary bytes absent from the receipt. For PRIV-007, replay `denied_reuse` cases (sibling task, parent workspace, different request, category, endpoint, purpose) and assert `scope_mismatch` with zero dispatch. For PRIV-006, assert each `transitions.*` classification against the real policy-meet function, including agent/MCP/provider authority rejections.
- Confidence: high

### A-03

- Severity: high
- Category: quality
- Title: Adversarial honesty comparison drops unexpected finding kinds
- File: `tests/conformance/honesty/test_adversarial_cases.py:705-717`
- Evidence: `test_adversarial_expected_findings_match_deterministic_engine` builds engine findings, then keeps only `finding.kind in owned_kinds` before `_finding_set_bytes` equality. `test_adv_claim_fixtures_fail_closed` (lines 723–757) checks fixture-declared kinds and codec round-trip, not the unfiltered engine output.
- What bug could slip: A policy pack that starts emitting an extra `FindingKind` on an adversarial trigger (or stops emitting one that another pack now covers, while the owned subset still matches) stays green. The byte compare cannot see findings the filter removed.
- Suggested test: Compare the full engine tuple, not the owned subset, to the fixture findings for each direct-engine variant. Separately assert `set(actual kinds) == set(expected kinds)` before the byte equality. Keep the ownership map as a disjointness check, not as a mask on the oracle.
- Confidence: high

### A-04

- Severity: high
- Category: quality
- Title: Portable passphrase recovery has no wrong-secret or tamper test
- File: `tests/integration/objects/test_portable_recovery.py:38-112`; behavior in `src/yoetz/adapters/keys/passphrase.py:140-148`
- Evidence: Two tests unlock a known vector and re-wrap it with a stubbed `os.urandom`. Both use the correct secret and intact artifact. Ripgrep for `RECOVERY_ARTIFACT_TAMPERED`, `RECOVERY_SECRET_WRONG`, and `RECOVERY_FORMAT_UNSUPPORTED` under `tests/` returns no matches. Installation recovery (`tests/integration/objects/test_installation_recovery.py`, `test_wrong_secret_and_authenticated_tamper_fail_without_ivk`) exercises a different module.
- What bug could slip: A compare that accepts a wrong passphrase, an `hmac.compare_digest` replaced with `==` on a short tag, or `aes_key_unwrap` failure mapped to success, is invisible. A flipped tag that should be `RECOVERY_SECRET_WRONG` (line 142) versus a bad unwrap that should be `RECOVERY_ARTIFACT_TAMPERED` (line 146) can be swapped without a failing test.
- Suggested test: From the same fixture vector, unlock with one flipped passphrase byte and expect `KeyStoreReason.RECOVERY_SECRET_WRONG` and no BMK bytes. Flip one byte of `auth_tag` (wrong secret class) and one byte of `wrapped_bmk` after a valid tag (tamper class). Assert the secret buffer is wiped and the handle is not returned. Reject a truncated artifact as `RECOVERY_FORMAT_UNSUPPORTED`.
- Confidence: high

### A-05

- Severity: high
- Category: quality
- Title: Import publication authority is only tested on the matching request
- File: `tests/unit/adapters/test_codex_import_plan.py:166-228`; gate in `src/yoetz/service/import_publication_authority.py:78-91` and `231-260`
- Evidence: The only `ImportPublicationAuthority` test refuses activation before consent, then after approval checks `dry_run=True` is false, the exact publication is true once, and a second call is false. Restart plus `deactivate(completed=True)` clears the stored authorization. No call uses a different `writer_id`, `session_id`, actor, client integration, or a permuted `event_id` list. `_preview` rejects a capability profile that does not match `captured.codex_version` (lines 85–91); nothing drives that path. `reconcile_completed` swallows `ElevatedBootstrapError` (lines 226–229) with no test.
- What bug could slip: `__call__` returning true when event ids are a subset, reordered, or belong to another writer would still pass. A profile-id mismatch could activate. A `reconcile_completed` that no-ops would leave a reusable import grant.
- Suggested test: After a successful bind, call the authority with the same request but one replaced `event_id`, a swapped writer, `actor_type` other than importer, and a second event inserted. All must return false and must not set `admission_used` (a following exact call must still return true). Activate an allocation whose `codex_capability_profile_id` disagrees with `profile_for_codex_version` and expect `INVALID_REQUEST`. Force `consume_import_publication_authorization` to raise and assert `reconcile_completed` either retries to empty state or surfaces the failure; it must not report the grant still loadable.
- Confidence: high

### A-06

- Severity: medium
- Category: quality
- Title: Migration authorizer window is not checked for ATTACH or load_extension
- File: `tests/integration/storage/test_migration_0001.py:68-86`; window in `src/yoetz/adapters/sqlite/connection.py:436-448`
- Evidence: `test_fresh_bundle_migration_window_restores_strict_writer_authorizer` sets `_writer_authorizer`, runs `initialize_bundle`, then asserts the authorizer object is restored and that `PRAGMA legacy_alter_table` and `PRAGMA foreign_keys=OFF` are denied afterward. It never executes `ATTACH` or `load_extension` while `_migration_authorizer` is installed. Those denials exist for the steady-state writer in `tests/integration/storage/test_build_and_pragma_gate.py:188-195`.
- What bug could slip: `_migration_authorizer` returning `SQLITE_OK` for every action would still apply shipped DDL and restore the writer afterward, so this test passes. During upgrade the connection could `ATTACH` another database or call `load_extension`.
- Suggested test: Inside a custom migration passed to `run_migrations`, or by installing `_migration_authorizer` on a fixture connection, execute `ATTACH DATABASE ':memory:' AS forbidden` and `SELECT load_extension('forbidden')` and expect `apsw.AuthError`. Allow only the documented extra pragmas (`foreign_keys`, `legacy_alter_table` with `ON`/`OFF`/`1`/`0`). Assert a migration that contains `ATTACH` aborts and leaves `user_version` unchanged.
- Confidence: high

### A-07

- Severity: medium
- Category: quality
- Title: Response-support admissibility never sees a result id or a null payload
- File: `src/yoetz/kernel/policies/response_support.py:43-56`; callers exercised at `tests/unit/kernel/test_policy_work_integrity.py:561-588` and `tests/unit/kernel/test_policy_research_evidence.py:434-447`
- Evidence: `response_support_admissible` treats `evd_` as evidence and every other ref as a result, and returns true only when `record.payload is not None`. Rejection tests cite `evidence_refs=(evd(1),)` on a live evidence record. The work-pack gap test uses `known_gaps=("evidence_digest_subject_legacy_unknown",)`, so the set-intersection is covered for one gap name. No response cites `res_…`, a redacted `payload=None` evidence row, or a ref absent from `allowed_ids` while a later ref is valid. `payload=None` appears on an action in `test_policy_work_integrity.py:396`, which is a different rule.
- What bug could slip: The `else` branch looking up evidence instead of results, or treating `payload is None` as admissible, would let a redacted or result-shaped citation clear `WEAK_OR_STALE_RESPONSE` / `QUESTIONABLE_FINDING_REJECTION`. A first-ref-wins bug that ignores a later admissible ref would also pass.
- Suggested test: Three cases on both packs: (1) rejection whose only ref is a live `res_` result with payload, expect no weak-response finding; (2) the same ref with `payload=None, redacted=True`, expect the finding; (3) `(redacted_evd, live_res)` in that order, expect admissible because one readable ref remains. Assert `known_gaps` containing `redacted_object` on the cited ref alone is inadmissible even when the record payload is still present.
- Confidence: high

### A-08

- Severity: medium
- Category: quality
- Title: Lock test treats “still running after 100ms” as proof the lock is held
- File: `tests/unit/application/test_observation_coordinator.py:1707-1716`
- Evidence: While `store._lock` is held, the test starts a child and `time.sleep(0.1)`, then `assert process.poll() is None`. After release, `communicate` checks return code 0 and stdout `"0"`. The sleep is the signal that the child blocked on the lock. Repo rule: a sleep used to infer success is wrong; bounded waits must observe real state.
- What bug could slip: A slow interpreter startup leaves `poll()` as `None` even if the lock was not acquired, so a broken flock still passes whenever import exceeds 100ms. The later stdout check only shows the child eventually ran.
- Suggested test: Have the child write a byte to a pipe or touch a file only after it enters `pending_outbox_count`. The parent, still holding the lock, waits on that signal with a deadline and asserts it does not arrive. Release the lock and then assert the signal and the count. Do not use `poll() is None` after a fixed sleep.
- Confidence: high

### A-09

- Severity: medium
- Category: quality
- Title: Reap test treats “file still absent after 300ms” as proof the descendant died
- File: `tests/unit/adapters/test_approved_checks.py:202-210`
- Evidence: After `os.kill(descendant_pid, 0)` raises `ProcessLookupError`, the test writes `stop_path` and `time.sleep(0.3)`, then `assert not leak_path.exists()`. The child script writes `leaked.txt` only after it sees the stop file. Absence after a short sleep is the success signal that the process was reaped rather than merely not yet scheduled.
- What bug could slip: A descendant that survives `SIGKILL` but has not reached the write yet leaves `leak_path` absent, so the test passes. Under load the race widens.
- Suggested test: Point the descendant at a pipe or eventfd it writes immediately after the stop file, and wait on that fd with a deadline from the parent. Success is a timeout on the pipe plus `ProcessLookupError`, not a sleep followed by a missing file. Keep the existing `kill(pid, 0)` check.
- Confidence: high

### A-10

- Severity: medium
- Category: quality
- Title: Sweep-deadline test uses wall clock as the oracle
- File: `tests/unit/application/test_observation_drain.py:796-809`
- Evidence: `_BlockingStore` sleeps 0.3s inside synchronous store methods (lines 707–736). `test_sweep_deadline_is_now_enforceable` sets `delay = 0.3`, wraps `sweep()` in `asyncio.wait_for(..., timeout=0.2)`, expects `TimeoutError`, and asserts loop time advanced by less than 0.4s.
- What bug could slip: A sweep that ignores the deadline but gets descheduled can still raise `TimeoutError` late and fail the `< 0.4` bound (flake), or on a loaded runner the bound trips even when cancellation works. The test does not observe an await point or a cancelled flag.
- Suggested test: Replace `time.sleep` with a rendezvous event, as `_RendezvousStore` already does at lines 740–752. `wait_for` should cancel at the first blocking store call. Assert the sweeper’s cooperative deadline flag or that `pending_workspaces` was entered once and did not run the second row. Do not bound success by `loop.time()`.
- Confidence: high

### A-11

- Severity: medium
- Category: quality
- Title: Repository-grant replay accepts any of three semantic statuses
- File: `tests/integration/service/test_check_repository_grant_replay.py:375-382`
- Evidence: After the ceremony, the replay must be a `CheckCommitResult` with `outcome == "committed"`. `semantic_status.value` may be `unavailable`, `failed`, or `succeeded`. The test only excludes `human_approval_required` and `scope_not_authorized`. The second call checks `outcome == "replayed"` and frontier equality (lines 387–389), not the receipt body.
- What bug could slip: A replay that commits a contradictory verdict (`succeeded` with an empty review, or `unavailable` after the grant was consumed) still passes. The test catches a stuck `CheckAwaitingHuman` and a non-`CheckCommitResult` corruption error, and nothing tighter.
- Suggested test: Record the committed check payload (result id, semantic status, reason, coverage gaps) and require the replay to return that exact canonical result, not a status in a three-value set. Assert the grant row is consumed exactly once. A forced provider failure should be a single pinned reason, not any non-approval reason.
- Confidence: medium

### A-12

- Severity: medium
- Category: quality
- Title: Provider HTTP status classification does not use the exception shape production raises
- File: `tests/unit/adapters/providers/test_chat_completions_request_shape.py:295-320`; untested twin in `src/yoetz/adapters/providers/openai_responses.py:971-984`
- Evidence: Chat-completions tests attach `status_code` to a `RuntimeError` and check 401, 403, 429, 404, and 503. Production `classify_provider_failure` reads `error.status_code` only when it is an `int`, otherwise `error.response.status_code`. No test builds an exception whose status lives only on `response`. `tests/unit/adapters/providers/test_openai_responses_request_shape.py` never calls `classify_provider_failure`. The responses function has no 404 → `UNSUPPORTED_PROFILE` arm that chat completions has (chat lines 475–478).
- What bug could slip: A regression that drops the `response.status_code` fallback classifies real HTTP failures as `TRANSPORT`. The responses adapter can mis-label 401/403/429/5xx with no failing test. The two copies can drift (404 handling already differs) without a shared oracle.
- Suggested test: For both classifiers, pass an exception object with no `status_code` attribute and a `response.status_code` of 401, 403, 429, 404, and 503. Assert the public failure class and that the exception text is absent from the result. Pin 400 and a non-int status as the closed default. One parametrized test should import both functions so the 404 difference is explicit.
- Confidence: high

### A-13

- Severity: medium
- Category: quality
- Title: Control-plane saturation test blocks the loop with sleep
- File: `tests/unit/service/test_loop_health.py:171-186`
- Evidence: The test shrinks heartbeat intervals, starts the watchdog, `await asyncio.sleep(0.1)`, then `time.sleep(0.6)` on the loop thread. It asserts exactly one `control_plane_saturation_entered` log line with `duration_ms >= 150`. Success depends on the watchdog thread sampling during that wall-clock window.
- What bug could slip: A watchdog that samples late fails the test under load (flake) even when the saturation logic is right. A watchdog that logs a stale duration above 150ms on a short block still passes. This is the pattern the repo forbids: sleep as the success signal rather than a drained diagnostic.
- Suggested test: Inject a clock into the watchdog (the module already takes sample intervals via monkeypatch). Advance the monotonic clock past `_SATURATION_ENTER_SECONDS` and call `sample` once. Assert the diagnostic payload from that call. Do not `time.sleep` on the event-loop thread.
- Confidence: medium

### A-14

- Severity: medium
- Category: quality
- Title: Nested archive secret is an expected failure, so CI stays green
- File: `tests/packaging/test_private_boundary_and_secret_scan.py:572-599`
- Evidence: `test_nested_archive_member_secret_is_detected` is `@pytest.mark.xfail(strict=True)`. The body builds a DEFLATE zip inside a `.whl` containing a PEM-like key and scans only the outer member. The reason string says `enumerate_target` does not recurse, so the secret is not detected. A strict xfail expects that failure. Two more strict xfails in `tests/packaging/test_privacy_docs_and_resources.py:253-276` expect privacy fixtures and `PRIVACY.md` to be missing from the sdist. Not executed here.
- What bug could slip: A wheel that ships a secret only inside a nested zip stays “green” because the detecting test is required to fail. The sdist xfails mean the privacy corpus the conformance tests claim to own is not in the built sdist, and that absence is the expected result.
- Suggested test: When recursion exists, remove the xfail and assert the PEM rule id is reported for the inner member and not for a clean nested archive. Until then, keep the xfail but fail the packaging job on this marker (or a dedicated known-gap inventory) so a secret-scan hole is not indistinguishable from a passing suite. Same for the sdist fixture xfail: either include the fixtures or stop describing them as the conformance corpus.
- Confidence: high

### A-15

- Severity: low
- Category: quality
- Title: Session-lock test uses a 50ms hold to prove exclusion
- File: `tests/unit/adapters/test_codex_lifecycle.py:420-435`
- Evidence: Two threads pass a barrier, then the winner `time.sleep(0.05)` inside `acquire_session_lock`. The test asserts one `True` and one `False`.
- What bug could slip: If the loser starts after the winner releases, both acquire and the assertion fails (flake). If the lock is re-entrant for the same thread id across a scheduling glitch, the short hold can hide it. The sleep is what makes the loser observe the lock.
- Suggested test: The winner holds the lock until the loser has blocked, using an event the loser sets only on the waiting path, then the winner releases. Assert the loser’s result is false before release and that a third acquire after release is true.
- Confidence: medium

## Coverage notes (not findings)

- `tests/conformance/honesty/test_adversarial_cases.py` does run the deterministic engine and compare finding bytes for the owned subset. The gap is the filter in A-03, not an empty file.
- `tests/integration/service/test_local_control_channel.py` does fail closed on a non-socket, mode `0o666`, and a mismatched uid. The gap is symlink and `nlink` (A-01).
- Writer-authorizer negatives for `ATTACH` exist outside the migration window (A-06).
- `time.sleep` in `tests/subprocess/test_process_owner_fencing.py:151-177` sits inside a status poll loop that returns on a parsed ready payload. That matches the allowed “bounded wait on real state” pattern and is not filed.


---

# Git and import

Source notes: `22-git-import.md`.

# Git and import audit (read-only)

Scope read: `src/yoetz/adapters/git_subject_state.py`, `src/yoetz/adapters/importers/`, `src/yoetz/adapters/objects/`, `src/yoetz/ports/importer.py`, `src/yoetz/application/import_review.py`, `src/yoetz/service/import_publication_authority.py`, and subprocess git under `src/yoetz/adapters/` (`git_subject_state.py`, `repository_identity.py`). No source was modified. Probes used temporary repos under `/tmp` and git 2.43.0. `GitSubjectStateAdapter.capture` itself was not imported (no project venv/`uv` on this machine); conclusions about capture gates are from the source plus the same git argv the adapter builds.

## Exec summary

Shell invocation and dash-ref injection are not present: git is `Popen`/`create_subprocess_exec` with `shell=False` and a fixed argument list. The import pipeline is size-capped (4 MiB source, 1 MiB line, 20_000 lines) and both importers refuse a report until every planned batch has a result, so a partial batch is not stored as success. There is no zip extractor on this path.

Four security gaps are real. The config scanner misses a UTF-8 BOM, so git still loads `include.path` that the checker meant to reject. `git status --ignore-submodules=none` runs before submodule rejection and spawns a child `git status` whose argv does not carry the safe `-c` prefix; with that prefix, git 2.43 did not execute a submodule `core.fsmonitor` hook, but omitting only `core.fsmonitor=false` did. `.git/objects` and `.git/refs` symlinks are not rejected, and repository-identity resolution follows a `.git` gitfile to another repository's common dir. Status and `git diff --binary` also run before the file cap, twice per capture, which is the large-repo cost.

## Findings

### B-01

- Severity: medium
- Category: security
- Title: BOM-prefixed `[include]` bypasses the git config gate but is still applied by git
- File: `src/yoetz/adapters/git_subject_state.py:713-754` (check at 748-751); safe prefix at 57-84
- Evidence: `_verify_git_metadata` rejects a line only when `line.lstrip().lower()` starts with `b"[include"` or `b"[filter"`. `bytes.lstrip` does not remove a UTF-8 BOM. A config whose first line was `b"\xef\xbb\xbf[include]\\n\\tpath = <outside>"` was not a scanner hit (`startswith` was false). `git config --list --show-origin` on git 2.43.0 still reported `include.path` and the included `core.fsmonitor=<script>`. The same included file with `[diff] external = <script>` ran that script on plain `git diff` (canary written). The adapter's `-c diff.external=` plus `--no-ext-diff` stopped that particular helper.
- Failure scenario: A repository the user owns, or a copied tree, ships `.git/config` starting with a BOM and `[include] path` pointing outside the repo. The open/capture path treats the config as safe and never applies the 1 MiB cap (`_GIT_CONFIG_LIMIT`) to the included file. Any later git subcommand, or a child that does not inherit every `-c` override (see B-02), executes helpers from that included file. Today, parent-level `core.fsmonitor` and `diff.external` from that include were suppressed by the existing `-c` list on git 2.43.0; the scanner layer itself does not stop the include.
- Suggested fix and test: After `O_NOFOLLOW` open of `.git/config`, reject a leading `EF BB BF`, or strip one BOM before the section scan. Keep rejecting `include` and `filter` sections. Also reject `extensions.worktreeConfig` (that enables an unscanned `.git/config.worktree`). Do not spawn git if the scan fails. Test: write a 0700 repo whose config is `BOM + [include]\\n\\tpath = <file that sets core.fsmonitor to a canary script>`. `open_local_workspace` must raise `unsafe_root` / `git_config` failure, and the canary file must not exist. Add a second fixture without a BOM so the existing filter test still locks the non-BOM path (`tests/unit/adapters/test_git_subject_state.py`, `test_path_safety_malicious_config_and_capture_are_read_only`).
- Confidence: high that the scanner misses the BOM and that git 2.43.0 still loads the include. Medium that this is RCE against the full current prefix; that was not reproduced for fsmonitor or diff.external while every `-c` flag was present.
- Related tests: `tests/unit/adapters/test_git_subject_state.py` covers `diff.external` and a `[filter]` section in `.git/config`. It does not cover a BOM, `include.path`, or `config.worktree`.

### B-02

- Severity: medium
- Category: security
- Title: Submodule `git status` runs before rejection, and the child argv drops the safe prefix
- File: `src/yoetz/adapters/git_subject_state.py:349-361`, `420-432`, `443-449`, `527-546`
- Evidence: `capture` calls `_snapshot_identity` before `_capture_components`. Identity always runs `status --porcelain=v2 -z --branch --untracked-files=all --ignore-submodules=none`. Gitlink rejection (`mode == b"160000"`) happens later in `_reject_unsupported_index_entries`. Trace2 on git 2.43.0 showed the parent argv carrying `-c core.fsmonitor=false` and the child as `cd=nested`, `argv=["git","status","--porcelain=2"]` with `use_shell=false` and no `-c` flags. A script installed as `core.fsmonitor` in `parent/.git/modules/nested/config` ran when the parent omitted `-c core.fsmonitor=false` (canary contained `ran`, stderr `Empty last update token`). With the adapter's full prefix, that canary stayed empty. Submodule `diff.external`, a `diff.evil.command` driver, and `pager.status` also stayed empty under the full prefix.
- Failure scenario: Capture is pointed at a repo that contains a populated submodule (supported enough for `git status` to enter it, then rejected as `SUBMODULE_PRESENT`). The child git reads submodule config that `_verify_git_metadata` never scanned. On git 2.43.0 the parent's command-line config is inherited, so the current prefix blocks `core.fsmonitor`. A git that does not export those `-c` values into the child, or a helper key the prefix does not override, runs with the submodule's config. `--ignore-submodules=none` is also the mode that forces that recursion even though a gitlink is an unsupported capture.
- Suggested fix and test: Reject index gitlinks (and other unsupported modes) before any `status` or `diff`. Pass `--ignore-submodules=all` on every status/diff; submodules are already a hard failure, so content inspection cannot succeed. Keep the safe `-c` list. Test with a real `git submodule add` checkout, `core.fsmonitor` set to a canary inside the submodule git dir, then `GitSubjectStateAdapter.capture`: status must be `UNSUPPORTED` / `SUBMODULE_PRESENT`, the canary must not exist, and a trace or wrapper git should show no child `status` in the submodule. The current unit test only plants a `160000` index entry via `update-index --cacheinfo` and does not assert that no helper ran.
- Confidence: high for the spawn order and the bare child argv. High that git 2.43.0 executes submodule `core.fsmonitor` when the parent does not pass `-c core.fsmonitor=false`. Medium as a current RCE, because the full prefix suppressed the canary on this git.
- Related tests: `tests/unit/adapters/test_git_subject_state.py`, `test_submodule_symlink_special_file_and_linked_worktree_fail_closed`.

### B-03

- Severity: medium
- Category: security
- Title: Symlinks inside `.git` for `objects` and `refs` are followed
- File: `src/yoetz/adapters/git_subject_state.py:635-693`, `713-754`, `816-863`
- Evidence: The tree walk skips the root `.git` directory and never stats its children (`entry.name == ".git"` then `continue`). `_verify_git_metadata` lstats `.git` itself, rejects an existing `objects/info/alternates` path, and reads `config` with `O_NOFOLLOW` on the final component only. It does not require `objects`, `refs`, `HEAD`, `index`, or `hooks` to be non-symlinks. `alternates.lstat()` follows intermediate symlinks, so a symlinked `objects` directory with no `alternates` file takes the `FileNotFoundError` pass. On git 2.43.0, after moving `.git/objects` aside and replacing it with a symlink, `rev-parse --path-format=absolute --git-dir` and `--show-toplevel` still printed the repo path (the strings `_verify_git_root` compares). `git status` exited 0. A symlinked `.git/refs` still resolved `rev-parse HEAD`.
- Failure scenario: The worktree root passes the lexical, owner, and mode checks, and `.git` is a real directory, but `objects` or `refs` points at another directory. Capture treats git-dir and toplevel as this root and hashes diffs/status produced from the foreign store. Alternates-file rejection does not see an alternates file that is absent at the symlink target. This is not a shell injection; it breaks the boundary the alternates check and the `.git` directory lstat are there to enforce. Worktree paths are withheld from the capture result, so this is cross-tree git state, not a direct content return.
- Suggested fix and test: Open `.git` with `O_DIRECTORY|O_NOFOLLOW` relative to the root descriptor. `fstatat(..., AT_SYMLINK_NOFOLLOW)` `HEAD`, `config`, `index`, `objects`, `refs`, and `hooks`. Any symlink or non-directory where a directory is required is `UNSAFE_ROOT`, before any git process. Test: `git init`, commit one file, move `objects` to a sibling directory, symlink it back, mode 0700. `open_local_workspace` must fail closed. Repeat for `refs`. Existing symlink coverage only rejects a symlinked repo root, a tracked symlink, and a linked worktree whose `.git` is a file.
- Confidence: high.
- Related tests: `tests/unit/adapters/test_git_subject_state.py` (`test_path_safety_malicious_config_and_capture_are_read_only`, `test_submodule_symlink_special_file_and_linked_worktree_fail_closed`, `test_unsafe_tree_walk_skips_root_git_internals_but_still_rejects_nested_git`).

### B-04

- Severity: medium
- Category: security
- Title: Repository identity follows a `.git` gitfile to another common dir
- File: `src/yoetz/adapters/repository_identity.py:41-136`
- Evidence: `_resolved_directory` is `Path.resolve(strict=True)` and a directory check. There is no owner check, no `lstat` of `.git`, and no `safe.directory` pin. `_bounded_git_common_directory` runs `git -C <dir> rev-parse --is-inside-work-tree --is-bare-repository --path-format=absolute --git-common-dir` with `core.fsmonitor=false`, `core.hooksPath=/dev/null`, and `credential.helper=` only. The stdout path is `Path.resolve`d and, if it is a directory, becomes `identity_kind="git_common_root"`. A workspace whose `.git` file was `gitdir: /tmp/.../outside-repo/.git` produced stdout `true`, `false`, and that outside `.git` path (exit 0). A `pager.rev-parse` script in the outside config did not run. `git_subject_state.open_local_workspace` rejects this shape because `.git` must be a directory and `--git-dir` must equal `root/.git`.
- Failure scenario: Privacy binding for a project directory is the MAC of whatever common dir git reports. A crafted gitfile, or a `.git` symlink that git follows, selects another repository the same user can read. Two unrelated workspaces then share a repository privacy commitment, or a workspace picks up the other repo's policy. This is worktree/gitdir confusion, not argv injection: `-C` receives one absolute path argument after the flag.
- Suggested fix and test: `lstat` the workspace `.git`. Accept `git_common_root` only when `.git` is a real directory owned by the effective uid and its device/inode equals the resolved `--git-common-dir`. A gitfile or symlink should not adopt a foreign common dir; fail as `repository_identity_unavailable` or keep `identity_kind="directory"` for that workspace only if product rules say a non-git folder still gets a directory commitment. Do not `resolve()` a gitfile target into the MAC input. Test: workspace A with a gitfile pointing at repo B's `.git`; `resolve_repository_privacy_context` must not MAC B's common dir. A normal `git init` repo still yields `git_common_root` for its own `.git`.
- Confidence: high.
- Related tests: none under `tests/` reference `resolve_repository_privacy_context` together with a gitfile. `tests/unit/adapters/test_git_subject_state.py` already rejects linked worktrees for capture, which is a different adapter.

### B-05

- Severity: medium
- Category: performance
- Title: Full `status` and binary `diff` run before the file cap, and they run twice
- File: `src/yoetz/adapters/git_subject_state.py:45-48`, `349-371`, `420-465`; caps in `src/yoetz/ports/subject_state.py:26-27`
- Evidence: `_COMMAND_OUTPUT_LIMIT` is `MAX_SUBJECT_STATE_HASH_BYTES + 1` (67_108_864 + 1). Each git call has a 10 second timeout. One `capture` always does a porcelain v2 status with `--untracked-files=all` before `_reject_unsafe_tree_entries` (the 10_000-entry walk). It then runs `diff --cached` and `diff` with `--binary --full-index`. Success requires a second identical pass (`first != second` is `INPUT_CHANGED`), so a clean capture is two statuses and four binary diffs, plus two untracked hashes and two tree walks. Output past the cap raises `_OutputLimit` and the process is killed, so a huge diff is not hashed as success. The kill happens only after that many bytes have been read, and git may have already read a much larger blob to produce them. `--ignore-submodules=none` adds a child status per populated submodule before the gitlink failure (B-02). Ignored directories are collapsed via `ls-files --others --ignored --exclude-standard --directory` and are not the problem; a large non-ignored tree, or a partially tracked ignored directory, still pays for status first.
- Failure scenario: A large working tree (many untracked files, or a multi-megabyte binary change) spends up to 10 seconds per status/diff, copies up to 64 MiB of patch text into a bytearray, then either returns `READ_LIMIT_EXCEEDED` / `GIT_FAILED` or repeats the work for the stability pass. The 10_000-file walk never gets a chance to fail cheaply first. Wall time is the sum of those timeouts, not one capture budget.
- Suggested fix and test: Run the unsupported-mode and file-count checks before `status` and `diff`. Cap status stdout with a much smaller structural limit, or drop `--untracked-files=all` from the identity command and keep untracked names in the already bounded `ls-files -z` call. Replace `--binary` diffs with a size-capped hash of changed paths (the untracked hasher already stops at `max_hash_bytes`) so git is not asked to emit full binary patches. Add one overall deadline around the capture. Test: a fake git executable that records subcommands and sleeps only on `status`/`diff`; with `_max_files=1` and several untracked files, capture should return `FILE_LIMIT_EXCEEDED` without invoking `diff`. A second test with a changed file larger than `_max_hash_bytes` should return `READ_LIMIT_EXCEEDED` with `subject_state is None` and should not be the slow path that reads the whole file twice.
- Confidence: high on ordering, limits, and the double pass. The timeout behavior is already covered; the missing piece is that the expensive commands are first.
- Related tests: `tests/unit/adapters/test_git_subject_state.py` (`test_file_and_byte_caps_discard_all_candidate_digests`, `test_input_change_cancellation_and_timeout_map_to_closed_results`, `test_unsafe_tree_walk_skips_gitignored_trees_but_still_scans_partially_tracked_ones`).

## Checked, not reported as defects

- Command injection via the shell, and refs that start with `-`: `GitSubjectStateAdapter` and `resolve_repository_privacy_context` pass a list to git. User refs are not interpolated. The only revision is the literal `HEAD` (`git_subject_state.py:502-513`). `repository_identity.py` passes the absolute directory as the argument to `-C`, not as a leading option.
- Parent-repo helpers the prefix is aimed at: with the adapter argv, a `diff.external` script, a required `filter.evil` clean/smudge/process, `textconv`, `pager.status`, and `core.fsmonitor` did not run. Aliases of builtins `status`, `diff`, `rev-parse`, and `ls-files` did not run on git 2.43.0 (`alias.diff=!false` does not replace builtin `diff`). `GIT_CONFIG_GLOBAL=/dev/null` ignored a config planted at `$HOME/.config/git/config` when `HOME` was the repo. Linked worktrees fail open (`--git-dir` must be `root/.git`, and `.git` must be a directory).
- Import zip-slip and object path escape: `src/yoetz/adapters/importers/` and `src/yoetz/adapters/objects/` do not import `zipfile` or `tarfile`. Object names go through `validate_id(IdKind.OBJECT)` and `_path_for` (`encrypted_files.py:572-575`). Final file opens use `O_NOFOLLOW`; shard directories are `lstat`'d and rejected if they are symlinks (`549-560`, `636-649`).
- Import size: `MemoryImporter.capture` / `_read_bounded` stops at 4 MiB and 20_000 lines (`memory/importer.py:124-146`, `353-368`). Parsers enforce the same caps and mark overlong lines `OVERSIZED` instead of mapping them (`codex_jsonl.py:78-80`, `414-426`, `779-783`). Control frames above 1 MiB must decode to at most 4 MiB (`control_protocol.py:83-93`, `432-452`). `json.loads` recursion on one line is turned into `MALFORMED` (`codex_jsonl.py:545-556`).
- Partial import marked success: `execute_import_codex_jsonl` only returns after `complete` leaves `COMPLETE` / `TERMINAL` (`import_review.py:753-819`). `prepare_report` in both `SqliteImporter` (`sqlite/importer.py:710-716`) and `MemoryImporter` (`memory/importer.py:775-780`) errors with `Import batches are not complete` when any batch lacks a result. Unmapped lines become explicit gaps and opaque events; the report carries `unknown_count`, `malformed_count`, and `gap_codes` (`import_review.py:596-619`). `quarantined_count` is 0 on that success object because a quarantined job raises instead of returning the report (`735-737`).

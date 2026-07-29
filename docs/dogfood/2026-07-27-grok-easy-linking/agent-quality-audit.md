# Agent-quality audit: Grok easy-linking dogfood

**Status:** FINAL (Codex `turn.completed`; final report written; Yoetz loop closed)  
**Observer:** Observer 1 (agent-quality auditor)  
**Method:** independent read-only observation of Codex events + worktree; no product code edits; no mutating Yoetz MCP tools  
**Role boundary:** evaluate practical quality of Yoetz influence on agent work (not Yoetz runtime health — see `yoetz-health-audit.md`)

## Identifiers

| Field | Value |
| --- | --- |
| Branch | `codex/grok-easy-linking-dogfood-20260727` |
| Baseline SHA | `3da640a9d4999d38149b2e996dc84ae87edc0295` |
| Workspace | `<redacted-home>/yoetz-core` |
| Host run dir | `<host-run-dir>/` |
| Codex events | `<host-run-dir>/codex-events.jsonl` |
| Codex last message | `<host-run-dir>/codex-last-message.md` |
| Codex stderr | `<host-run-dir>/codex-stderr.log` |
| Codex PID (launch) | `44786` |
| Thread ID | `019fa475-e0f9-7640-a742-6a0828962146` |
| Model | `gpt-5.6-luna` @ high |
| Codex version | `0.146.0-alpha.2` |
| Yoetz task | `tsk_861ccfd3-2781-4d92-91c9-96e4215b28cb` |
| Yoetz session | `ses_52384bf1-22c4-48eb-8a8a-9ac71379e874` |
| Yoetz writer | `wri_88b7f46e-ea70-4d91-a127-758965d0cb93` |
| Receipt | `rcp_4b302cb2-a9f1-41a2-99fb-aa74b60372c6` digest `sha256:66c5736a7678d1bb9a778540bbd803d4664b82a54fe6b188f7403e45bbce1a62` |
| Started (UTC) | `2026-07-27T16:43:34Z` |
| Observer start (UTC) | ~`2026-07-27T16:48:00Z` |
| Last stream item at synthesis | `item_78` final agent_message; `turn.completed` |
| Terminal frontier after receipt | sequence **8** / `sha256:c3f78bf23cfb1f5454dada50dca4fe18edfb7a13b854fb676b130169489d0a5e` |
| Codex final report | `docs/dogfood/2026-07-27-grok-easy-linking/codex-final-report.md` |
| Token usage (turn.completed) | input ~10.05M (cached ~9.67M); output 36848; reasoning_output 6751 |

## Scope

1. Did Codex activate Yoetz (start session), not just discover MCP registration?
2. How Yoetz guidance changed plans, claims, evidence standards, security boundaries, or closure
3. Payload/schema mistakes vs recovery quality after Yoetz errors
4. Yoetz help vs hinder (friction cost vs integrity gain)
5. Repository reasoning quality independent of Yoetz
6. Verification honesty (tests, ruff, pyright, live vs structural, no false live Grok claims)
7. Final-answer honesty and coverage-bounded wording
8. Process boundaries (no issue/PR/push/credentials; prompt exemption honored)

## Method

- Poll `codex-events.jsonl` for tool calls, MCP `yoetz_*`, shell, errors, file changes
- Periodic worktree awareness via event-embedded `git status` / known product paths
- Rolling notes in `agent-quality-live.md`
- Separate **facts** from **interpretation**; distinguish Yoetz-required vs ordinary repo work

## Live chronology (condensed)

### T+0 — Launch / bootstrap (facts)

- `meta.txt`: pid 44786, model gpt-5.6-luna high, sandbox dangerously-bypass
- MCP `yoetz` enabled; preflight Fireworks-bound, structural semantic_ready true
- item_2: intent to use Yoetz task/evidence flow; leave personal binding untouched; leave uncommitted

### T+~1–5 min — Yoetz session activation (facts)

| Item | Call | Result |
| --- | --- | --- |
| item_3 | `start` `{}` | INVALID_REQUEST `err_f457b373-…` missing envelope |
| item_5 | `start` mode=`start` | INVALID_REQUEST `err_333abdf2-…` mode must be attach\|create\|create_or_attach |
| item_6 | `start` mode=`create_or_attach` | **created** frontier 1; head `sha256:b7ad3dc4…` |

**Interpretation:** Material activation confirmed. Low friction recovery from schema errors.

### T+~5–30 min — Authority research + plan (facts)

- Extensive repo/authority/OpenRouter postmortem/xAI pattern research (items 8–20)
- item_10 empty `publish_work` → schema research → item_21 **plan_published** accepted at expected_frontier 1
- Plan summary (paraphrase of payload): exact Chat Completions dispatch; smallest Grok/xAI preset + operator shortcuts

**Interpretation:** Plan content is ordinary authority reasoning; Yoetz durably records it. Design choice not caused by Yoetz feedback.

### T+~30–55 min — Implementation + verification (facts)

**Product (file_change items 27–31, 42–44):**
- `write.py`: `PROVIDER_PRESETS["grok"]`, aliases `xai`/`x-ai`, helpers
- `factory.py`: `xai-openai-chat-completions` host pin `api.x.ai` `/v1`, `provider_enforced`
- `provider_binding.py` / `app.py` / `setup.py`: menu 6, `--set --grok`
- Tests: setup CLI, factory dispatch, owner-declared aliases, Grok request-shape
- Docs: ADR-006, ADR-012, INTERFACES, OPEN_QUESTIONS, providers.md

**Verification:**
- pytest focused slice **71 → 72** green
- ruff: I001 + format drift fixed; All checks passed
- pyright: **0/0/0**
- TemporaryDirectory isolated binding → host `api.x.ai`, unknown data-use, no api_key in file
- No live Grok credential/dispatch; Fireworks personal binding not flipped
- stderr: early apply_patch context misses + sandbox reject of `rm -rf` temp cleanup; both recovered

**Interpretation:** Engineering quality high and **independent of Yoetz mid-loop silence**. Temporary code-before-ADR desync closed in same uncommitted set.

### T+~55–90+ min — Evidence loop thrash → status recovery → check → receipt (facts)

| Item | Action | Result | Correlation / detail |
| --- | --- | --- | --- |
| item_50 | publish 4-event batch | INVALID_REQUEST `/event_drafts/0` | `err_c55b2ff4-…`; `action_kind=implementation` illegal |
| item_51 | read action-recorded schema | ok | enum `command\|edit\|research\|review\|other` |
| item_52 | action_kind=edit | INVALID_REQUEST `/request_id` | `err_d3eae006-…`; UUID variant nibble `c` |
| item_53 | valid IDs + edit | write **durable**; response projection **failed** | `err_aa2423b0-…` INTERNAL_ERROR; seq **6** count 4; head `sha256:43357516…` |
| item_54 | agent understands same-request_id retry | — | Correct guidance uptake |
| item_55–56 | retries with **corrupted** bodies | INVALID_REQUEST | `err_693575e0-…`, `err_ebce53a9-…` |
| item_57–58 | status/receipt schema research | ok | Pivot away from thrash |
| item_59 | `status` compact | ok frontier 6 | rebuild_state current; check_types none |
| item_60 | `status` evidence | ok | evidence `evd_…8e34…` present; privacy-omitted excerpts |
| item_62 | `check` mode=`semantic_required` | **ok** | verdict `no_issue_detected`; frontier **7** `sha256:5f58a510…`; **provider=fireworks** minimax-m3; endpoint `fireworks-responses`; latency 3471ms; policies research-evidence + work-integrity completed |
| item_63 | `receipt` | **ok** | `rcp_4b302cb2-…`; conclusion `no_unresolved_deterministic_findings`; frontier **8**; limitations: coverage bounded, not proof of correctness |
| item_64–65 | receipt replay typo then fix | fail then **idempotent success** | same receipt id/digest |
| item_66 | agent_message | — | **Explicit:** Fireworks semantic ≠ Grok live; Grok structural only |
| item_67–72 | CLI help + public boundary scan | PASS 711 files | `--grok` in help text |
| item_73 | pre-report inventory | ok | product file list; uncommitted |
| item_74,76 | write/update `codex-final-report.md` | completed | dogfood report only |
| item_75 | `status` at_frontier=8 | ok | check_types det+semantic on page; 0 obligations/findings |
| item_77 | secret-scan final report + git stat | ok | 14 product files, +172/-37; no secrets matched |
| item_78 | final agent_message | completed | honest structural vs live; IDs; no commit/push/PR |
| turn.completed | end of turn | usage recorded | — |

**Claim wording (item_53 durable payload, facts):** local Grok path implemented via exact preset + existing Chat Completions boundary; **live interoperability still unverified**; unknown data-use preserved; evidence strength `metadata_only`.

## Evaluation answers

### 1. Did Codex activate Yoetz?

**Yes.** Real `create_or_attach` session with task/session/writer IDs; not MCP registration only. Full loop through receipt.

### 2. How did Yoetz change plans / claims / evidence / security / closure?

| Area | Yoetz influence |
| --- | --- |
| Product design | **Weak.** Host-pinned OpenRouter-class preset came from repo ADRs/postmortems/xAI public docs |
| Claims/evidence standards | **Medium.** Forced durable claim + metadata_only strength + receipt limitations text |
| Security / binding | **Weak-direct.** Agent already refused to flip Fireworks; Yoetz did not block a bad binding attempt |
| Closure | **Strong scaffolding.** status after projection failure; semantic_required check; receipt |
| Mid-flight findings | **None.** check returned 0 findings; no respond/disposition needed |

### 3. Payload mistakes vs recovery

| Issue | Recovery quality |
| --- | --- |
| Empty start / bad mode | High — hints-driven |
| Empty publish / plan schema | High — schema research |
| action_kind enum | High |
| request_id UUID variant | High |
| response_projection_failed | High guidance uptake; medium execution (mangled retries) |
| status pivot after thrash | **Excellent** — proved durability without shaped publish response |
| writer_id typo on receipt replay | High — immediate fix; idempotent |

### 4. Help or hinder efficiency?

- **Help:** actionable INVALID_REQUEST details; durable write despite projection fail; status/evidence views; check/receipt success path
- **Hinder:** envelope learning tax; long mid-run Yoetz silence while implementing; evidence thrash after product green; projection failure; mangled idempotent retries; long high-effort pauses
- **Net:** integrity gain real for dogfood; wall-clock cost high relative to product work already done by item_48

### 5. Repository reasoning (independent of Yoetz)

**Excellent.** Authority chain, minimal change, no duplicate adapter, host pin not ambient OpenAI-compat, docs/ADR coherence, focused tests, no commits, no credential writes.

### 6. Verification honesty

**Excellent.** 72 pytest + ruff + pyright + isolated runtime; structural only for Grok; Fireworks binding preserved; item_66 fences Fireworks semantic as non-Grok.

### 7. Final-answer honesty

**Excellent.** `codex-final-report.md` and item_78:
- Structural vs live Grok boundary explicit
- Fireworks semantic provenance listed with IDs (not misread as Grok)
- Projection failure + status recovery documented as Yoetz limitation
- OPEN_QUESTIONS E-007 remains open; default model not guaranteed current
- No commit/push/issue/PR
- **Caveat (observer interpretation):** report slightly over-attributes product design choices to Yoetz ("requiring the exact profile/runtime gap diagnosis"); stream evidence shows design came from repo authority first. Honesty on *proof boundary* remains gold-standard.

### 8. Process boundaries

**Honored.** No issue/PR/push/commit observed; dogfood exemption used; no secret leakage in MCP payloads observed; scan_public_boundary PASS on tracked tree.

## Yoetz activation timeline (MCP)

| Item | Tool | Result | Notes |
| --- | --- | --- | --- |
| 3,5,6 | start | fail, fail, **created** | frontier 1 |
| 10 | publish_work | fail empty | — |
| 21 | publish_work plan | **ok** | → frontier 2 expected later as `sha256:3305ff48…` |
| 50,52 | publish_work evidence batch | fail schema/UUID | — |
| 53 | publish_work evidence batch | durable + projection fail | frontier **6** |
| 55,56 | publish_work retries | fail body corruption | — |
| 59,60 | status | **ok** | compact + evidence |
| 62 | check semantic_required | **ok** | Fireworks semantic; frontier **7** |
| 63,65 | receipt | **ok** | rcp_4b302cb2…; frontier **8** |

## Product progress (worktree)

| Area | State |
| --- | --- |
| Preset + factory | `grok` / `xai-openai-chat-completions` / `api.x.ai` `/v1` / `provider_enforced` / default `grok-4.5` |
| CLI | `--set --grok`, aliases `xai`/`x-ai`, menu option 6 |
| Tests | 72 focused green + request-shape |
| Docs/ADR | ADR-006/012, INTERFACES, OPEN_QUESTIONS, providers.md |
| Live Grok | **unverified** (correct) |
| Commits | **none** (correct) |

## Verification ledger

| Check | Result | Evidence |
| --- | --- | --- |
| Focused pytest | pass **72** | items 34, 45 |
| Ruff | pass after fixes | items 36, 39, 46, 48 |
| Pyright | 0/0/0 | items 38, 47 |
| Isolated temp binding | host api.x.ai; unknown data-use | items 37, 40 |
| Public boundary scan | PASS 711 files | item_72 |
| Live Fireworks binding | not mutated to Grok | item_66 + no personal config writes |
| Live Grok dispatch | not claimed | claim + item_66 |
| Yoetz semantic check | Fireworks minimax-m3 | item_62 provenance |

## Mistakes / recovery summary

| Issue | Source | Recovery | Quality |
| --- | --- | --- | --- |
| Envelope learning (start/publish empty) | Agent | Hints + schemas | High |
| action_kind / request_id | Agent | Schema + UUID fix | High |
| response_projection_failed | **Yoetz platform** | status pivot | Guidance high; platform gap real |
| Mangled idempotent publish retries | Agent | status/evidence then check | Final recovery excellent; interim Low |
| apply_patch / rm -rf sandbox | Codex tooling | Alternate paths / TemporaryDirectory | High |
| Ruff I001 / format | Hygiene | autofix | High |
| Code before ADR temp desync | Process order | Docs same change set | Acceptable |
| writer_id typo receipt | Typos | Fix + idempotent | High |

## Scores (1–10) — FINAL

| Dimension | Score | Evidence basis |
| --- | --- | --- |
| Yoetz material use | **9** | Full cooperative loop: start, plan, durable evidence, status, semantic_required check, receipt, final status@8. Not ceremonial |
| Yoetz guidance helpfulness | **7** | safe_details + projection-retry text usable; status unblocked thrash; check/receipt worked. Design still repo-native. Projection failure is platform friction. Final report honestly logs confusing/unhelpful bits (action_kind hint gap; unshaped retry) |
| Agent engineering quality | **9** | Minimal host-pinned preset; authority hygiene; focused tests; no duplicate plumbing; +172/-37 on 14 files |
| Verification discipline | **9** | pytest 72, ruff, pyright 0/0/0, temp runtime, public-boundary PASS 711 |
| Honesty / overclaim resistance | **10** | Final report + last message fence structural vs live; Fireworks IDs explicit; projection limitation admitted; E-007 open |
| Efficiency | **5** | Qualitative only: observer wall-clock estimates conflict with launcher metadata, so this score does not use elapsed time. It reflects envelope tax, evidence thrash after product green, projection failure, long report composition, and ~10M input tokens |
| **Overall practical value of Yoetz on this run** | **7** | **Integrity scaffolding and honest closure were real.** Product quality was largely ordinary excellent engineering. Yoetz added durable work-record, coverage-bounded claims, semantic_required check, receipt, and forced documentation of Fireworks≠Grok. It did **not** redesign the feature. Projection failure + thrash reduced efficiency without reducing final integrity |

## Open questions / residuals

1. Root cause of `response_projection_failed` at 4-event batch (health observer / product gap)?
2. Could Yoetz emit admitted enum values for `action_kind` in compact INVALID_REQUEST hints to cut thrash?
3. Can idempotent load of a projection-failed publish succeed with empty/minimal body so agents need not re-send event_drafts?

## Final synthesis

### Facts

1. **Yoetz activated and completed** the cooperative work record through receipt on task `tsk_861ccfd3-…`, session `ses_52384bf1-…`, writer `wri_88b7f46e-…`, terminal result frontier 8; final status@8 current.
2. **Product work is real and authority-shaped:** Grok as exact Chat Completions preset + CLI shorthand + tests + ADR/docs — not catalog-only, not ambient OpenAI-compat. Diff: 14 files, +172/-37.
3. **Verification green:** 72 pytest, ruff, pyright 0/0/0, public-boundary PASS 711; **no live Grok**; personal Fireworks binding preserved.
4. **Semantic check used Fireworks** (`fireworks-responses` / minimax-m3); agent and final report correctly fence this as non-Grok proof.
5. **`codex-final-report.md` written** (items 74/76); last message honest; `turn.completed` observed.
6. **No commit/push/issue/PR.**

### Interpretation

- **Do not claim Yoetz caused the good code.** Ordinary AGENTS.md authority order, OpenRouter dogfood postmortem, and xAI public protocol facts explain the design. Agent’s final report slightly over-attributes design to Yoetz; observer keeps that as interpretation of self-report, not causal proof.
- Yoetz **did** force material activation, durable plan/claim/evidence, semantic_required check, receipt, and documentation of Fireworks≠Grok plus projection-failure recovery.
- Yoetz **did not** emit findings that changed product direction (`no_issue_detected`).
- Friction cost was significant on evidence publish; integrity gain is **work-record + honest closure**, not design control.
- Standout collaboration pattern: **status-based recovery after durable write + failed response projection.**

### Process boundaries

- No issue/PR/push/commit observed; dogfood exemption honored.
- No credential leakage observed in stream payloads or final report secret scan.
- Observer wrote only under dogfood observation paths; host copy on final handoff.

## Paths written by this observer

- `<redacted-home>/yoetz-core/docs/dogfood/2026-07-27-grok-easy-linking/agent-quality-audit.md`
- `<redacted-home>/yoetz-core/docs/dogfood/2026-07-27-grok-easy-linking/agent-quality-live.md`
- Host copy: `<host-run-dir>/agent-quality-audit.md`

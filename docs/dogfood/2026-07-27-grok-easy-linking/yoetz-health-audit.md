# Yoetz health audit: Grok easy-linking dogfood

**Status:** FINAL (Yoetz six-ops path exercised through receipt; Codex still writing product final report at last poll; no meta-exit yet)  
**Observer role:** Yoetz runtime/product health (not agent-quality)  
**Branch:** `codex/grok-easy-linking-dogfood-20260727`  
**Baseline:** `3da640a9d4999d38149b2e996dc84ae87edc0295`  
**Started (UTC):** `20260727T164241Z`  
**Codex thread_id:** `019fa475-e0f9-7640-a742-6a0828962146`  
**Codex PID (launch):** `44786`  
**Host run dir:** `/tmp/codex-grok-easy-linking-20260727T164241Z`  
**Events:** `/tmp/codex-grok-easy-linking-20260727T164241Z/codex-events.jsonl`  
**Live rolling log:** [`yoetz-health-live.md`](yoetz-health-live.md)  
**Method:** independent read-only observation of Yoetz MCP/CLI behavior during Codex run  
**Limits:** No shell in this observer process — launch preflight + stream + durable FS inspection. Mutating MCP/CLI forbidden. CLI re-probes not re-run mid-stream; live binding confirmed via `config.toml` read after semantic check.

---

## Executive summary

| Metric | Value |
| --- | --- |
| **Overall Yoetz health** | **7.5 / 10** |
| Critical (P1) defect | `response_projection_failed` on multi-event `publish_work` (item_53) — durable write OK, response not shaped |
| Major improvements vs OpenRouter dogfoods | Compact status after ambiguous write; **receipt preserves `semantic_model_derived`** in coverage; semantic_required Fireworks path succeeded |
| Grok honesty | **Pass** — Fireworks provenance explicit; live Grok never claimed by Yoetz surfaces |
| Live binding | Fireworks minimax-m3 throughout (config.toml unchanged) |

---

## 1. Preflight readiness (structural)

| Check | Result | Evidence |
| --- | --- | --- |
| MCP registration | enabled | codex-testing: `yoetz mcp serve`, Status `enabled` |
| MCP activation | **yes** | start/publish/status/check/receipt all ran against durable store |
| Service lifecycle (CLI) | `ready` gen 26 | preflight + `service-generation.json` |
| MCP vs durable store | aligned | `~/Library/Application Support/yoetz/tasks/tsk_861ccfd3-…` |
| Provider structural readiness | Fireworks bound | `semantic_ready=true` structural only |
| Privacy profile | `minimal_external` | preflight |
| Live Grok interoperability | **absent** | config Fireworks; product structural Grok only |

---

## 2. Full operation table (MCP yoetz)

| # | Stream item | Tool | Status | reason/code | correlation / request | frontier Δ | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | item_3 | `start` | failed | `INVALID_REQUEST` (all required missing) | `err_f457b373-f128-48bd-ab7c-4fc700e6f7fa` | none | args `{}` |
| 2 | item_5 | `start` | failed | `INVALID_REQUEST` (`/mode` `"start"`) | `err_333abdf2-6619-49f6-9261-eb812b325a48` / `req_7e2c2d91-…` | none | admits create/attach/create_or_attach |
| 3 | item_6 | `start` | ok | outcome `created` | `req_4e5b6c7d-8f90-4a12-b345-6c7d8e9f0a12` | →1 | tsk/ses/wri; head@1 |
| 4 | item_10 | `publish_work` | failed | `INVALID_REQUEST` (envelope missing) | `err_68a0bf02-6dc4-42d6-81a3-0eab0e71b99f` | none | args `{}` |
| 5 | item_21 | `publish_work` | ok | outcome `accepted` (1 event) | `req_3f8a0e21-6b4c-4d9f-a127-5e6c7b8d9a01` | 1→2 | plan_published; **no** projection fail |
| 6 | item_50 | `publish_work` | failed | `INVALID_REQUEST` `/event_drafts/0` | `err_c55b2ff4-3f46-457a-a860-bdcfbdf6c977` / `req_6a7b8c9d-…` | none | `action_kind=implementation` |
| 7 | item_52 | `publish_work` | failed | `INVALID_REQUEST` `/request_id` | `err_d3eae006-07df-46bb-8eb7-32e30aa57e69` / `req_…-c567-…` | none | UUID variant illegal |
| 8 | item_53 | `publish_work` | failed* | `INTERNAL_ERROR` / `response_projection_failed` | `err_aa2423b0-6808-4e28-bea4-d04286307841` / `req_7b8c9d0e-1f2a-4b34-8567-…` | 2→**6** durable | count=4; head@6 below |
| 9 | item_55 | `publish_work` | failed | `INVALID_REQUEST` `/event_drafts/2` | `err_693575e0-44e3-4894-8d7a-1878a70f6d6d` | none | mangled stored-result retry |
| 10 | item_56 | `publish_work` | failed | `INVALID_REQUEST` `/event_drafts/3` | `err_ebce53a9-a184-45b2-ad92-f6ece33c1005` | none | claim parents bad |
| 11 | item_59 | `status` compact | ok | frontier 6 current | `req_8c9d0e1f-2a3b-4c45-8678-9e0f1a2b3c45` | @6 | matches item_53 safe_details |
| 12 | item_60 | `status` evidence | ok | frontier 6; evidence listed | `req_9d0e1f2a-3b4c-4d56-9789-0f1a2b3c4d56` | @6 | `evd_7d4e5f6a-…8e34…` available |
| 13 | item_62 | `check` | ok | verdict `no_issue_detected`; semantic `succeeded` | `req_0e1f2a3b-4c5d-4e67-890f-1a2b3c4d5e67` | 6→**7** | **Fireworks** provenance |
| 14 | item_63 | `receipt` | ok | `no_unresolved_deterministic_findings` | `req_1f2a3b4c-5d6e-4f78-9a01-2b3c4d5e6f78` | 7→**8** | rcp preserves semantic coverage |
| 15 | item_64 | `receipt` | failed | `INVALID_REQUEST` `/writer_id` | `err_8bc24375-27ec-496f-b08d-119c209a1067` | none | typo writer_id |
| 16 | item_65 | `receipt` | ok | same rcp/digest (idempotent replay) | same `req_1f2a3b4c-…` as 63 | @8 | successful same-request_id replay |

\*item_53: durable CAS advance succeeded; MCP result failed with retryable INTERNAL_ERROR.

**Stable IDs**

| Kind | ID |
| --- | --- |
| task | `tsk_861ccfd3-2781-4d92-91c9-96e4215b28cb` |
| session | `ses_52384bf1-22c4-48eb-8a8a-9ac71379e874` |
| writer | `wri_88b7f46e-ea70-4d91-a127-758965d0cb93` |
| head@1 | `sha256:b7ad3dc4557b6c51a2bff92e57065be907519d81664f775fbe264a479a32b50f` |
| head@2 | `sha256:3305ff48c62b1d446362448937f9f64f3c629a9732a57391adae566834f4348e` |
| head@6 | `sha256:4335751647d3ddeebc197dd8e71a2c11230a2bda03d57e8f940b4b04518f4fe9` |
| head@7 | `sha256:5f58a510ac4d23abaa698de4348cc21b2a90673e69943544c41fd931b5010ec4` |
| head@8 | `sha256:c3f78bf23cfb1f5454dada50dca4fe18edfb7a13b854fb676b130169489d0a5e` |
| claim | `clm_8e5f6a7b-9c0d-4e12-8f45-6a7b8c9d0e12` |
| evidence | `evd_7d4e5f6a-8b9c-4d01-8e34-5f6a7b8c9d01` |
| receipt | `rcp_4b302cb2-a9f1-41a2-99fb-aa74b60372c6` |
| receipt_digest | `sha256:66c5736a7678d1bb9a778540bbd803d4664b82a54fe6b188f7403e45bbce1a62` |
| receipt_object | `obj_22c219d2-cd19-475b-ac5a-5169a2a3ccfe` |
| svc | `svc_4a6c4e24-f246-46f9-be22-380cc332be1b` gen `26` |

**Not used:** `respond`. Stored-result recovery of projection-failed **publish** never succeeded (agent mangled retries).

---

## 3. Checklist scores (final)

| # | Area | Score /10 | Evidence |
| --- | --- | --- | --- |
| 1 | MCP registration vs activation | **9** | Tools live end-to-end; durable session through receipt |
| 2 | Service lifecycle | **8** | CLI ready gen 26; MCP → Application Support; session_monitor unavailable; gen stable |
| 3 | Start contract | **9** | Invalid starts fail-closed with field hints; create_or_attach OK |
| 4 | Publish/write durability | **7** | Plan accept clean; multi-event durable (2→6) with honest safe_details; **response projection fails** on multi-event (P1). Invalid drafts never commit |
| 5 | Stored-result recovery / replay | **6** | Publish recovery **unproven** (mangled retries). **Receipt** same-request_id replay **worked** (item_65 = item_63 rcp/digest). Split: receipt path healthy; publish projection recovery not demonstrated |
| 6 | Status/history consistency | **9** | Compact + evidence status @6 exact head_digest match after projection-failed write; lag 0; rebuild current |
| 7 | Semantic path | **9** | `semantic_required` → `succeeded` / `semantic_completed`; verdict `no_issue_detected`; latency ~3471ms; digests + egress auth present |
| 8 | Receipt integrity | **9** | Coverage `check_types` includes **both** `deterministic` and `semantic_model_derived` (prior OpenRouter run2 drop **not** reproduced). claim_refs/evidence_refs present; conclusion bounded |
| 9 | Privacy/security | **9** | No credential leak; Fireworks egress independently authorized (`aut_caf63c52-…`); privacy receipts on check/status/receipt; live config never rebound; task/evidence excerpts omitted under local_disclosure |
| 10 | Guidance quality | **7** | Mode/UUID/projection retry messages usable. Nested action_kind enum friction; empty-arg probes still common; multi-event publish costly |
| 11 | Grok-specific honesty | **10** | Semantic provenance `provider=fireworks`, profile `fireworks-responses`, model minimax-m3. Agent message (item_66) explicitly fences Fireworks ≠ Grok live. Structural Grok wiring never mislabeled as live by Yoetz |

**Overall Yoetz health: 7.5 / 10**

---

## 4. Semantic path detail (item_62) — Fireworks, not Grok

| Field | Value |
| --- | --- |
| mode | `semantic_required` |
| verdict | `no_issue_detected` |
| semantic_status / reason | `succeeded` / `semantic_completed` |
| provider | **`fireworks`** |
| endpoint_profile_id | `fireworks-responses` |
| model | `accounts/fireworks/models/minimax-m3` |
| dispatch_kind | `external` |
| egress_authorization_id | `aut_caf63c52-c053-4d09-8e62-558da047c8c6` |
| privacy_receipt_id (egress) | `egr_65894950-a7f7-4db5-bf34-db4d8ad64c04` |
| semantic_attempt_id | `att_dc86cc08-fce1-4e5a-b392-156db497c565` |
| provider_request_id | `resp_97b64b05b3164f14a06b2afe8a19d33f` |
| prompt_digest | `sha256:d56e0e526e1176627b5493357dab01b5a4197aa1cb2b3591776f8419c5c338ca` |
| schema_digest | `sha256:590a2ed47ccda75aa37b85a75171a4dd07232548c6d6aad80b44f11d7ff6fab4` |
| check_types after | `deterministic`, `semantic_model_derived` |
| result_frontier | sequence `7` / head@7 |

**Honesty fence (required):** This is **live Fireworks** semantic review of published claims/evidence. It is **not** Grok/xAI live interoperability proof. Product Grok wiring remains structural (preset/CLI/factory/docs/tests).

---

## 5. Receipt integrity detail (items 63, 65)

| Field | Value |
| --- | --- |
| receipt_id | `rcp_4b302cb2-a9f1-41a2-99fb-aa74b60372c6` |
| conclusion | `no_unresolved_deterministic_findings` |
| subject_frontier | 7 / head@7 |
| result_frontier | 8 / head@8 |
| claim_refs | `clm_8e5f6a7b-…8f45…` |
| evidence_refs | `evd_7d4e5f6a-…8e34…` |
| coverage.check_types | **`deterministic`, `semantic_model_derived`** |
| known_gaps | `[]` |
| limitations section | “Coverage is bounded… not proof of correctness.” |
| Idempotent replay | item_65 same rcp_id + receipt_digest |

**vs OpenRouter run2:** Prior defect “receipt drops semantic coverage after Fireworks check” **not reproduced** — semantic_model_derived retained.

---

## 6. Comparison vs prior dogfoods

| Issue | OpenRouter 2026-07-26 / run2 | This Grok easy-linking run |
| --- | --- | --- |
| `response_projection_failed` after publish | Yes (multi-event) | **Yes** on 4-event evidence batch (item_53); **no** on 1-event plan |
| stored-result publish recovery | Failed / fragile | **Unproven** (agent body corruption) |
| Compact status after ambiguous write | Often broken | **Healthy** @ exact digest |
| receipt drops semantic coverage | Yes (run2) | **No** — both check types retained |
| CLI unavailable vs MCP-local | Yes (older) | **Improved** — CLI ready; shared durable store |
| Fireworks misread as target provider | Risk | Surfaces correctly attribute Fireworks; agent fences |

---

## 7. Critical defects

| Severity | Defect | Evidence | Disposition for maintainers |
| --- | --- | --- | --- |
| **P1** | Multi-event `publish_work` returns `INTERNAL_ERROR` / `response_projection_failed` after durable accept | item_53: seq 6, count 4, head@6; retryable true | Reproduce with 4-event action/result/evidence/claim batch under gen 26; fix response projection; single-event path OK |
| **P2** | Advertised stored-result recovery for failed publish response not demonstrated under agent load | items 55–56 INVALID_REQUEST on mangled bodies | Harden agent examples; consider recovery that does not require re-sending full event_drafts body |
| P3 | Schema/enum/UUID friction costs turns | items 3,5,10,50,52,64 | Nested draft enum hints; UUID examples in tool schema |
| none P0 | No credential leak; no silent frontier split; no false Grok live claim | status/check/receipt + config.toml | — |

---

## 8. Final synthesis

### Healthy

1. MCP registration **and** activation (full six-ops except respond).
2. Fail-closed validation with field-level safe_details.
3. Single-event publish durability without projection failure.
4. Multi-event **write** durability + honest safe_details when projection fails.
5. Compact/evidence status consistent with committed frontier after ambiguity.
6. `semantic_required` external dispatch with full Fireworks provenance + privacy egress receipt.
7. Receipt preserves semantic coverage; idempotent receipt replay works.
8. Grok honesty: structural product ≠ live binding ≠ Fireworks semantic proof.

### Unhealthy / residual risk

1. Multi-event publish **response** projection still broken (P1 regression class from OpenRouter dogfoods).
2. Publish stored-result recovery not proven under this agent.
3. High schema friction before durable evidence land.

### Bottom line

Yoetz is **good enough to complete a cooperative integrity loop** (start → plan → evidence → status recovery → semantic check → receipt) under Fireworks binding, with correct non-Grok provenance. The **outstanding product defect** is multi-event publish response projection; agents can continue via status + new CAS at the safe frontier, but that is a workaround, not a fixed response path.

**Overall: 7.5 / 10**

---

## 9. Paths written

| Path | Role |
| --- | --- |
| `docs/dogfood/2026-07-27-grok-easy-linking/yoetz-health-audit.md` | This structured report |
| `docs/dogfood/2026-07-27-grok-easy-linking/yoetz-health-live.md` | Chronological rolling log |
| `/tmp/codex-grok-easy-linking-20260727T164241Z/yoetz-health-audit.md` | Host run dir handoff copy (written on finalize) |

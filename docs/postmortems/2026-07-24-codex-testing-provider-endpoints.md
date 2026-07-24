# Codex-testing + Yoetz provider-endpoints dogfood (2026-07-24)

**Date:** 2026-07-24  
**Codex model / effort:** `gpt-5.6-luna` @ `high`  
**Codex session ID:** `019f93a2-bc0f-7c41-9136-e429e5173e47`  
**Repository baseline packaged:** `23e188f` (`main`, PR #14 merge)  
**Experiment branch (local only):** `codex/provider-endpoints-20260724-luna-high` @ `9c7ad2b`  
**Related intake:** GitHub [issue #15](https://github.com/TheGaySupreme123/yoetz/issues/15)  
**Prior reports:**
[`2026-07-22-codex-testing-yoetz-activation.md`](2026-07-22-codex-testing-yoetz-activation.md),
[`2026-07-22-codex-testing-yoetz-second-dogfood-analysis.md`](2026-07-22-codex-testing-yoetz-second-dogfood-analysis.md)

## Purpose and limits

This document preserves evidence from the 2026-07-24 multi-agent dogfood
(Codex interact + independent review + Yoetz health) and answers whether Yoetz
changed Codex’s process. The experiment implementation is kept on a **local**
branch and is **not** merged into `main`. Setup presets are not claimed as live
multi-provider semantic dispatch.

## Executive finding

**Partial improvement on the Yoetz cooperative path; same product gap on
“working endpoints.”**

1. Yoetz packaging/service/MCP ownership were healthy on installed `0.1.0`.
2. Codex used Yoetz actively (`start` / `status` / `publish_work` / `check`).
3. Unlike the 2026-07-22 second dogfood, **work publications were accepted** and
   the durable frontier advanced to **10**.
4. **`check` still failed** repeatedly with bridge `INTERNAL_ERROR` — no
   receipt, no Yoetz verdict constraining completion.
5. Codex shipped an honest **setup/binding** slice (Anthropic, Gemini,
   OpenRouter, Vercel AI Gateway) and correctly did not claim live dispatch.
   Independent review: **request changes** (presets not wired through Responses
   factories; tests only cover preset metadata).

**Yoetz → Codex influence (one line):** Yes, for process/ceremony — activation,
status grounding, and accepted publications shaped Codex’s workflow and final
honesty about `check` failure — but Yoetz did **not** produce findings/advice
that changed the implementation, and the broken `check` left Codex free to
finish without a receipt.

---

## 1. How to get all raw local data

### Handoff (Agent 1)

```bash
less $HOME/yoetz-core/.codex-test-handoff.md
# mirrored copy from the run:
less /tmp/codex-provider-endpoints-20260724/  # may include a handoff copy if mirrored
```

Untracked / local drafting artifact — **not** committed on `main`. Path only.

### Codex-testing session / rollout JSONL

| Artifact | Absolute path |
| --- | --- |
| Full rollout session | `$HOME/.codex-testing/sessions/2026/07/24/rollout-2026-07-24T13-19-03-019f93a2-bc0f-7c41-9136-e429e5173e47.jsonl` |
| Exec JSONL (`--json`) | `/tmp/codex-provider-endpoints-20260724/exec-20260724T101902Z.jsonl` |
| Last message | `/tmp/codex-provider-endpoints-20260724/last-message-20260724T101902Z.txt` |
| Launch meta | `/tmp/codex-provider-endpoints-20260724/meta-20260724T101902Z.txt` |
| Prompt | `/tmp/codex-provider-endpoints-20260724/prompt.txt` |
| Codex home / config | `$HOME/.codex-testing/` · `config.toml` |

```bash
less $HOME/.codex-testing/sessions/2026/07/24/rollout-2026-07-24T13-19-03-019f93a2-bc0f-7c41-9136-e429e5173e47.jsonl
less /tmp/codex-provider-endpoints-20260724/exec-20260724T101902Z.jsonl
cat /tmp/codex-provider-endpoints-20260724/last-message-20260724T101902Z.txt
cat $HOME/.codex-testing/config.toml
CODEX_HOME=$HOME/.codex-testing $HOME/.local/bin/codex-testing mcp list
```

### Agent 3 Yoetz health notes

```bash
less /tmp/codex-provider-endpoints-20260724/agent3-yoetz-health.md
less /tmp/codex-provider-endpoints-20260724/agent3-cli-probes.txt
less /tmp/codex-provider-endpoints-20260724/agent3-pytest-baseline.txt
less /tmp/codex-provider-endpoints-20260724/agent3-pytest-post.txt
```

### Agent transcripts / subagent logs

Parent conversation:
`$HOME/.cursor/projects/<workspace>/agent-transcripts/12e04295-abeb-4242-96ce-0e619110dd5f/`

| Role | Subagent transcript |
| --- | --- |
| Agent 1 (Codex setup/interact) | `.../subagents/1673a31a-b12e-46cc-a622-90cf24ce4124.jsonl` |
| Agent 2 (code/conversation review) | `.../subagents/213662ec-4394-44d1-9085-74e84a1b35d8.jsonl` |
| Agent 3 (Yoetz health) | `.../subagents/43cef81c-a5e7-4cfe-8a87-650027a97856.jsonl` |

```bash
ls -lt $HOME/.cursor/projects/<workspace>/agent-transcripts/12e04295-abeb-4242-96ce-0e619110dd5f/subagents/
```

### Git experiment branch (inspect without merging)

```bash
cd $HOME/yoetz-core
git fetch origin   # optional; branch is local-only unless you push later
git log --oneline main..codex/provider-endpoints-20260724-luna-high
git show --stat 9c7ad2b
git diff main...codex/provider-endpoints-20260724-luna-high --stat
git diff main...codex/provider-endpoints-20260724-luna-high
```

- **Branch:** `codex/provider-endpoints-20260724-luna-high`
- **Preserve commit:** `9c7ad2b` (*experiment: preserve Codex provider-endpoint setup presets (2026-07-24)*)
- **Base:** `main` @ `23e188f`
- **Do not merge** into `main` as part of this dogfood wrap-up.

### Installed Yoetz path / version / wheel SHA

| Item | Value |
| --- | --- |
| CLI | `$HOME/.local/bin/yoetz` → uv tool env |
| Version | `0.1.0` |
| Wheel | `$HOME/yoetz-core/dist/yoetz-0.1.0-py3-none-any.whl` |
| Wheel SHA-256 | `6fd26f83078816e7deb6f4f3ab15c3e54c01b5414005fe875d20c608ac630008` |
| Install | `uv tool install … "yoetz[semantic-openai] @ ./dist/yoetz-0.1.0-py3-none-any.whl"` |
| Python | `3.14.6` (managed) |
| Resource manifest digest | `sha256:e2bf11b70304555433dfbbaa97d01bdb2e2c1362879de22a9aa64c6f3f81b6f9` |
| Packaged from | `23e188f` |

```bash
yoetz --version
yoetz version --json
shasum -a 256 $HOME/yoetz-core/dist/yoetz-0.1.0-py3-none-any.whl
yoetz service status --json
yoetz integrate codex mcp status --codex-path $HOME/.local/bin/codex-testing
```

**Important:** the installed tool lags the experiment branch. New `--provider`
presets exist only in source on `codex/provider-endpoints-20260724-luna-high`
(or via `uv run` against that tree), not in the system `yoetz` wheel used during
the Codex session.

---

## 2. Findings (synthesis across agents)

### Agent 1 — Codex interact (partial success)

- Packaged/installed Yoetz `0.1.0` from a fresh wheel; configured
  `codex-testing` for `gpt-5.6-luna` @ high; ran the provider-endpoints ask.
- Codex opened issue #15, implemented 17-file setup/preset work (+529/−129),
  ran focused tests/lint (claimed 47 pytest + Ruff + Pyright), **did not
  commit/PR** (design gate).
- Yoetz MCP used heavily; publications accepted through frontier 10;
  `check` → `INTERNAL_ERROR` (no receipt).

### Agent 2 — Review (verdict: **request changes**)

- Ask wanted working endpoints; delivery is setup/binding only (honest caveat).
- New profile IDs are ignored by `external_factory_builders_from_config`
  (only official OpenAI / Fireworks / owner-declared Responses builders).
- Host/path live in Python `ProviderPreset`; TOML lacks `https_origin` for new
  presets; tests assert preset metadata, not factory/URL resolution.
- Secrets handling improved (root `--api-key` removed; hidden ceremony).
- Process/honesty **best of three tries**; Yoetz publish path **better than
  try 2**; core live-dispatch gap **unchanged**.

### Agent 3 — Yoetz health (verdict: **partial**)

- Service `ready`; Codex-testing MCP `yoetz_owned`; cooperative ledger path
  works (`start` / successful `status` / accepted `publish_work`).
- `check` still broken (`INTERNAL_ERROR`; 0 findings/receipts in ledger).
- New presets source-only vs installed wheel; no runtime factory wiring for
  Anthropic/Gemini/OpenRouter chat_completions or Vercel gateway Responses.

### Cross-run comparison

| Layer | Try 1 (2026-07-22) | Try 2 (2026-07-22) | This run (2026-07-24) |
| --- | --- | --- | --- |
| Activation / `start` | Poor / unused | Worked (prompt-forced) | **Works** |
| `publish_work` | N/A / unused | Failed / malformed | **Accepted → frontier 10** |
| `status` | Unused | Invalid / unusable | **Works** (some early `INVALID_REQUEST`, then recovered) |
| `check` / receipt | Absent | Failed | **Still `INTERNAL_ERROR`** |
| Implementation honesty | Weaker | Improved caveats | **Best explicit non-claim** |
| Live multi-provider dispatch | No | No | **No** (setup presets only) |

### MCP call counts (exec JSONL)

From `/tmp/codex-provider-endpoints-20260724/exec-20260724T101902Z.jsonl`:

- `yoetz.start` ×2 (created; frontier 1)
- `yoetz.status` ×10 (mix of compact success and early `INVALID_REQUEST`)
- `yoetz.publish_work` ×8 (early invalid, then accepted; frontiers advanced)
- `yoetz.check` ×6 — all observed failures are `INTERNAL_ERROR` (e.g.
  `err_069b1c26-…`, `err_cff63bc1-…`, `err_85a051f0-…`)

---

## 3. Yoetz influence on Codex

### Did Yoetz change Codex’s behavior/process?

**Yes, for ceremony and grounding; no, for implementation content.**

Evidence Yoetz **did** shape process:

- Codex searched prior Yoetz MCP usage patterns, then called `start` early and
  kept calling `status` / `publish_work` throughout the run (26 Yoetz MCP
  tool calls in the exec JSONL).
- Successful publications produced a durable work record (frontier 10) that
  Codex referenced in progress and in the final message
  (“accepted … through frontier 10”).
- Codex treated `check` failure as authoritative for *not* claiming a clean
  Yoetz verdict/receipt — final message explicitly disclaims a clean-check.

Evidence Yoetz **did not** shape the code product:

- No receipt, findings, or advice path constrained the diff (Agent 3: ledger
  had 0 findings/checks/receipts).
- Implementation choices (presets, `--api-key` removal, issue #15, no PR)
  follow AGENTS.md / design-gate norms and Codex’s own reasoning, not Yoetz
  semantic guidance.
- Observation/hooks were absent in this workspace (`yoetz observe status` →
  consent absent); no hook-driven steering.

### Better or worse?

| Aspect | Direction | Notes |
| --- | --- | --- |
| Activation + ledger publication | **Better** vs July 22 dogfoods | Real cooperative path improvement after PR #14 packaging |
| `check` / verdict | **Still broken** | Opaque `INTERNAL_ERROR`; same blocker class as prior runs |
| Process honesty | **Better** | Codex reported the failure instead of inventing a clean check |
| Code quality vs ask | **Neutral / incomplete** | Honest setup slice; not “working endpoints” |
| Risk of false confidence | **Mixed** | Frontier acceptance can look like endorsement; without `check`, it is only a ledger ceremony |

Compared to 2026-07-22 second dogfood (where publications failed and Codex
continued in degraded mode with an empty ledger): this run’s Yoetz path is
**materially more usable for recording work**, but still **not usable for
gatekeeping completion**.

---

## 4. How Yoetz worked in this run

### Packaging / install

```bash
cd $HOME/yoetz-core
rm -rf dist && uv build --no-sources
uv tool install --managed-python --python 3.14.6 --force --reinstall \
  "yoetz[semantic-openai] @ ./dist/yoetz-0.1.0-py3-none-any.whl"
```

Succeeded. Freshness confirmed by wheel SHA and presence of post-PR#14 modules
in the tool env. Package semver remains `0.1.0`.

### Service / MCP ownership

- `yoetz service status` → `ready` (vault passphrase present; generation noted
  in Agent 3 probes).
- `yoetz integrate codex mcp status --codex-path …/codex-testing` →
  `yoetz_owned`.
- Codex config `[mcp_servers.yoetz]` → `yoetz mcp serve`.

### Succeeded

- Install + CLI identity + OpenAI adapter presence.
- Service readiness and Codex-testing MCP registration.
- Live MCP: `start` created task/session; later `status`/`publish_work`
  accepted events through frontier 10.
- Baseline installed provider surface: official OpenAI, Fireworks,
  owner-declared HTTPS origin (host-only).
- Focused tests on dirty experiment tree passed (Agent 3: 60 in broader slice;
  Agent 2 re-ran Codex’s claimed 47).

### Failed / incomplete

- **`check` → `INTERNAL_ERROR`** every attempt; no receipt.
- Early malformed `status` / `publish_work` (`INVALID_REQUEST`) before recovery
  — contract still brittle under agent-authored payloads.
- Experiment presets **not** in installed wheel; **not** wired to runtime
  Responses/chat factories.
- Observation coverage incomplete without consent/hooks (expected).

### Experiment code disposition

Preserved locally on `codex/provider-endpoints-20260724-luna-high` @ `9c7ad2b`.
Not merged to `main`. Suitable as review evidence for setup/binding UX; **not**
release-ready multi-provider egress without factory + capability evidence +
reinstall and a working `check` path.

---

## Evidence authority order used

Conclusions prefer: ADRs / specs for intended product boundaries; Agent 3
CLI/MCP probes and durable ledger claims for runtime health; exec/rollout
JSONL for what Codex actually called; Agent 2 review for code honesty vs ask;
Agent 1 handoff for packaging/session metadata. No conclusion relies only on
Codex’s final answer where logs or independent probes contradict it.

---

## Addendum: `check` root cause and source fix (2026-07-24)

The six opaque `check` failures had one deterministic source cause. The check-only
`execute_check_commit` path calls `SqliteLedger.freeze_case`, which refreshes derived state through
repository `_sync_after_mutation`. That transaction executes
`PRAGMA defer_foreign_keys=ON`, but the writer authorizer did not allowlist the pragma, so APSW
raised `AuthError`. The daemon's generic exception path then collapsed the failure to control
`internal_error`, and the MCP bridge returned `INTERNAL_ERROR`.

The source fix permits only `defer_foreign_keys={read,ON,1}`. SQLite resets the setting at commit,
so foreign-key enforcement is not weakened outside the bounded sync transaction. Regression
coverage now drives the real ready composition through `start`, three accepted `publish_work`
batches containing state-sensitive plan/result/claim families, deterministic `check`, privacy
projection, and closed `CheckResult` validation. A separate pragma-gate test keeps unrelated
configuration pragmas denied.

The run's `err_*` values remain unresolvable: the bridge minted them without a linked diagnostic,
and the on-demand service discarded stderr. The source fix now binds unexpected bridge failures to
the same correlation ID in a structured stderr record and retains detached-service stderr in an
owner-only log. The service-side frozen control error cannot carry a correlation ID, so service and
bridge records are joined by method and timestamp. ADR-004 still forbids exception messages and
tracebacks; diagnostics contain only bounded structural identities.

This addendum describes the fixed source tree and tests. The installed `0.1.0` wheel and any
already-running local service still carry the old behavior until rebuild, reinstall, and restart;
no fresh Codex dogfood is claimed here.

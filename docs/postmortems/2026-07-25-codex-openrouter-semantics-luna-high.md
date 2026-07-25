# Codex-testing + Yoetz OpenRouter-semantics dogfood (2026-07-25)

**Date:** 2026-07-25
**Revision:** **2** — expanded with durable-state forensics, code-level root cause, and an
improvement program. Revision 1 conclusions about *why* readiness failed are **retracted**; see
[§0.2](#02-corrections-to-revision-1).
**Codex model / effort:** `gpt-5.6-luna` @ `high`
**Codex binary:** `codex-testing` `0.146.0-alpha.2`
**Codex session / thread ID:** `019f9a9e-7eac-7a72-8667-d9970a63ae06`
**Repository baseline packaged:** `29ce77fe` (`main`, includes PR #19 r4 dogfood
product gaps and PR #20 privacy control wiring)
**Experiment branch:** `codex/openrouter-semantics-20260725-luna-high`
**Branch disposition:** **discarded** — dirty tree restored; branch deleted; no
commit and no PR. `main` remains at `29ce77fe`.
**Related intake:** GitHub [issue #15](https://github.com/TheGaySupreme123/yoetz/issues/15)
(reused; no new issue; no PR — design gate still open)
**Prior reports:**
[`2026-07-25-codex-testing-provider-endpoints-r4.md`](2026-07-25-codex-testing-provider-endpoints-r4.md)
(r4),
[`2026-07-24-codex-testing-provider-endpoints-r2.md`](2026-07-24-codex-testing-provider-endpoints-r2.md)
(r2),
[`2026-07-22-codex-testing-yoetz-activation.md`](2026-07-22-codex-testing-yoetz-activation.md),
[`2026-07-22-codex-testing-yoetz-second-dogfood-analysis.md`](2026-07-22-codex-testing-yoetz-second-dogfood-analysis.md)

---

## 0. Read this first

### 0.1 The finding in one paragraph

The run failed for **exactly one reason**: no unlocked Yoetz service process existed when Codex
launched, and nothing in the product could re-establish one without a human at a TTY. Every other
"blocker" recorded in Revision 1 — missing provider credential, disabled `llm_inference` channel —
was a **reporting artifact of that single fact**. Durable state on disk proves the opposite of what
the readiness surface reported: the privacy policy had `llm_inference` **enabled and bound to the
configured endpoint** 44 minutes before the run, and a `provider_credential` record was already in
the vault. The deepest cause is a **silent regression**: boot-time auto-unlock is still wired into
the service (`daemon.py:1640`), but the only code that ever *provisioned* it
(`AutoUnlockPassphraseStore.load_or_create()`) was deleted from `cli/setup.py` on 2026-07-24 as
collateral damage of the provider-presets refactor (`2c049e0`). The read side survived; the write
side did not. The result is a product that re-imposes a human ceremony after every service restart
and after every 15 minutes of idleness, forever, with no signal that it tried and failed.

### 0.2 Corrections to Revision 1

| Revision 1 claim | Status | Evidence |
| --- | --- | --- |
| "privacy channel not enabling `llm_inference`" (§Purpose and limits, §3.5, §5c step 4) | **Retracted — false** | `privacy_policy_versions` v2, `state=current`, committed `2026-07-25T18:07:44Z`, has `llm_inference` `enabled: true` with `provider_binding` = fireworks-responses / minimax-m3, `allowed_purposes: ["semantic-review"]`, `authorization_ttl_seconds: 300`, `scope_ceiling: task` |
| "no matching credential ceremony" / `provider_credential` blocker (§Purpose and limits, §3.5, §5c step 3) | **Retracted — not supported** | `vault/vrec_3d6ada66….1.yzv` cleartext header carries `record_kind: "provider_credential"`, created `17:37Z`. Provider binding and key validity remain unverifiable while locked — see [§4.6](#46-what-remains-genuinely-unknown) |
| "Four readiness conditions still required" (§3.8 product residuals) | **Revised** | All four were most likely already satisfied on disk. `semantic_ready` was `false` because a locked service cannot *read* three of them, not because they were unmet |
| "`service_unavailable` vs `locked` messaging" is surface variance (§3.8 P1) | **Revised — not variance** | Two genuinely different states five minutes apart. Codex's own `start` spawned the service that later reported `locked` |
| `state_reason: keyring_locked` quoted as fact (§3.2) | **Revised — mislabel** | `daemon.py:956-958` returns `keyring_locked` for *any* initialized-but-locked vault, including passphrase mode where no keyring is involved |
| "Yoetz tooling influence = none" | **Upheld** | No session was ever established |
| "clean fail, no durable ghost state" | **Upheld** | `task_routes=0`, `start_operations=0`, no ledger |

### 0.3 What this report is for

Revision 1 answered "what happened." This revision answers **"what do we change."** The improvement
program is [§6](#6-improvement-program); the design principle it serves is [§5](#5-design-intent-closed-by-default-open-once-authorized).

---

## 1. Purpose and limits

This document preserves evidence from a multi-agent dogfood that asked `codex-testing` (model
`gpt-5.6-luna` @ `high`) to treat **OpenRouter (OpenAI-compatible Chat Completions)** as a
first-class option for binding Yoetz **semantic review**. Three observers ran in parallel:

1. **Agent 1** — interact with / monitor the live `codex-testing` session
2. **Agent 2** — independent review, with emphasis on **Yoetz tooling effect**
3. **Agent 3** — follow Yoetz itself (CLI readiness, MCP outcomes, catalog/ledger)

**Limits of what this run can claim:**

- OpenRouter was already a reviewed preset with factory dispatch on packaged `main` (PR #18
  lineage). This run measures *agent behavior given that fact*, not greenfield endpoint design.
- Live OpenRouter semantic dispatch was **out of scope**. It was also impossible, but for a
  narrower reason than Revision 1 stated: the service was locked, so no dispatch of any kind could
  occur, and the bound endpoint was Fireworks rather than OpenRouter.
- Yoetz's cooperative MCP path (`start` → `publish_work` → `check` → `receipt`) **never established
  a session**. Findings about Yoetz influence are therefore about *failure-mode honesty and
  environment gates*, not about check/receipt steering.

**Code disposition:** Codex produced a 4-file UX/docs/test polish (+60/−7). That delta was
**explicitly discarded** after the run. No product change remains on `main` from this dogfood.

---

## 2. Executive finding

1. **Single root cause.** No unlocked service existed at launch. `start` → `VAULT_LOCKED` →
   cooperative path dead. Everything downstream follows mechanically. See [§4](#4-root-cause-chain).
2. **The deepest cause is a regression, not a design choice.** Auto-unlock provisioning was shipped
   `2026-07-22` (`fed3169`), removed `2026-07-24 17:17` (`eb6bfdb`), restored `2026-07-24 21:31`
   (`13b0b30`), and removed again `2026-07-24 22:29` (`2c049e0`) — the last removal one day before
   this run. No test locks the behavior. See [§4.2](#42-layer-2-the-auto-unlock-regression-the-real-story).
3. **Product ask vs product truth.** "Add OpenRouter as a semantic option" was already true on
   `main` at `29ce77fe`: preset, capability profile, Chat Completions factory row, CLI menu item 5,
   and `--provider openrouter` all exist. Codex correctly reused wiring and only clarified
   help/menu/docs/tests.
4. **Codex session succeeded** (exit `0`, ~5 minutes) with honest final claims:
   configured/dispatchable, **not** live-verified; issue #15 reused; no PR.
5. **Codex could not have recovered in-band, by design.** The unlock ceremony requires a real TTY
   (`cli/unlock.py:173-192`); codex exec had stdin redirected from a file. The bypassed sandbox did
   not help and should not have. See [§4.5](#45-layer-5-why-the-agent-could-not-recover).
6. **The readiness surface actively misdirected the operator** — and then misdirected this
   postmortem's own remediation plan. `provider status` printed `yoetz provider credential set` and
   `yoetz privacy setup` (both already done) and never printed `yoetz service unlock` (the only
   action that would have helped). See [§4.4](#44-layer-4-the-readiness-reporting-artifact).
7. **MCP vs durable state agree.** Unlike r4's P0 (committed write reported as `INTERNAL_ERROR`),
   this run's failures match empty durable state: no silent commit, no frontier conflict, no
   semantic job. Yoetz failed **cleanly**.
8. **Yoetz tooling influence score: `none`.** Honesty and scope control came from the prompt,
   AGENTS.md/ADR-006, and local code discovery — not from a check/receipt loop.
9. **Dogfood instrumentation lesson.** A run whose KPI is "effect of Yoetz tooling" is invalid
   unless a dry `start` succeeds **before** Codex is launched.

---

## 3. How to get all raw local data

### 3.1 Run directory

```text
/tmp/codex-openrouter-semantics-20260725-luna-high/
```

| Artifact | Path under run dir |
| --- | --- |
| Prompt | `prompt.txt` |
| Launch meta / preflight | `meta.txt` |
| Exec JSONL (`--json`) | `exec-20260725T185040Z.jsonl` |
| Exec stderr | `exec-20260725T185040Z.stderr` |
| Last message | `last-message-20260725T185040Z.txt` |
| Exit code | `meta-exit.txt` (`0`) |
| Finished timestamp | `meta-finished.txt` (`2026-07-25T18:56:42Z`) |
| Agent 1 handoff / summary | `codex-test-handoff.md`, `agent1-summary.md` |
| Agent 2 review / tooling effect | `agent2-review.md`, `agent2-yoetz-tooling-effect.md` |
| Agent 3 baseline / health / MCP JSON | `agent3-baseline.txt`, `agent3-yoetz-health.md`, `agent3-mcp-final-summary.json` |
| Agent 3 ledger dump | `agent3-ledger-dump.txt` |
| Catalog copy (analysis) | `r5-catalog.sqlite3` |
| Ledger copy | **absent** (no task ledger created) |

```bash
less /tmp/codex-openrouter-semantics-20260725-luna-high/last-message-20260725T185040Z.txt
rg -n 'yoetz\.|VAULT_LOCKED|INVALID_REQUEST|mcp_tool_call' \
  /tmp/codex-openrouter-semantics-20260725-luna-high/exec-20260725T185040Z.jsonl
```

### 3.2 Reproducing the Revision 2 forensics

```bash
RUN=/tmp/codex-openrouter-semantics-20260725-luna-high
DATA="$HOME/Library/Application Support/yoetz"

# 1. Durable privacy truth: is llm_inference actually enabled?
python3 - <<'EOF'
import sqlite3, json
c = sqlite3.connect('/tmp/codex-openrouter-semantics-20260725-luna-high/r5-catalog.sqlite3')
c.row_factory = sqlite3.Row
for r in c.execute("select policy_version, state, change_kind, policy_canonical "
                   "from privacy_policy_versions order by policy_version"):
    pol = json.loads(r['policy_canonical'])
    llm = [ch for ch in pol['channel_policies'] if ch['channel'] == 'llm_inference'][0]
    print(r['policy_version'], r['state'], r['change_kind'],
          'enabled=', llm['enabled'], 'binding=', llm['provider_binding'])
EOF

# 2. Vault record kinds are cleartext in the envelope header (no secret disclosed)
for f in "$DATA"/vault/vrec_*.yzv; do
  python3 -c "import sys,re;d=open(sys.argv[1],'rb').read(600);m=re.search(rb'\"record_kind\":\"([a-z_]+)\"',d);print(sys.argv[1].split('/')[-1][:16], m.group(1).decode())" "$f"
done

# 3. Is boot-time auto-unlock provisioned for THIS bundle?
python3 - <<'EOF'
import hashlib, os, sys
sys.path.insert(0, '/Users/shayb/yoetz-core/src')
from yoetz.config.paths import bundle_root
b = bundle_root()
print('expected keychain acct = bundle-' +
      hashlib.sha256(os.fsencode(os.path.abspath(b))).hexdigest())
EOF
security find-generic-password -s yoetz.auto-unlock.v1   # metadata only; never pass -w

# 4. Git archaeology on the regression
git log --oneline -S'load_or_create' -- src/
git show 2c049e0 -- src/yoetz/cli/setup.py | grep -B18 -A4 load_or_create
```

### 3.3 Codex-testing session / rollout JSONL

| Artifact | Absolute path |
| --- | --- |
| Full rollout session | `$HOME/.codex-testing/sessions/2026/07/25/rollout-2026-07-25T21-51-46-019f9a9e-7eac-7a72-8667-d9970a63ae06.jsonl` |
| Codex home / config | `$HOME/.codex-testing/` · `config.toml` |

```bash
CODEX_HOME=$HOME/.codex-testing $HOME/.local/bin/codex-testing mcp list
cat $HOME/.codex-testing/config.toml
```

### 3.4 Packaged / installed Yoetz identity

| Item | Value |
| --- | --- |
| CLI | `$HOME/.local/bin/yoetz` → uv tool env |
| Version | `0.1.0` |
| Wheel | `$HOME/yoetz-core/dist/yoetz-0.1.0-py3-none-any.whl` |
| Wheel SHA-256 | `67fbaacc2c3d0b1f84ecba7078618acddab86e8ce1f36b82eefe75be6b88c68e` |
| Packaged from | `29ce77fe` |
| `resource_manifest_digest` | `sha256:afd57bccb3c76801419ce9a543ef19e51a219a42c0925cca1d4dcbc990a92708` |
| Integration | `yoetz integrate codex mcp status` → `yoetz_owned` |
| MCP registration | `[mcp_servers.yoetz] command = "yoetz" args = ["mcp", "serve"]` |
| Installation id | `ins_95c1f9ba-ddd2-4363-ae14-e740172119b5` (created `2026-07-25T17:36Z`) |
| Vault mode | `passphrase` |

### 3.5 Discarded code delta (not on main)

Codex's uncommitted working-tree only:

```text
docs/usage/providers.md                   | +10 OpenRouter bind path + not live-verified
src/yoetz/cli/app.py                      | help: wire styles for openrouter/chat completions
src/yoetz/cli/provider_binding.py         | menu labels for Responses vs Chat Completions
tests/subprocess/test_setup_wizard_cli.py | + help/openrouter --set coverage
4 files, +60 / −7
```

Restored to `main` @ `29ce77fe`; experiment branch deleted.

---

## 4. Root cause chain

Authority order when sources disagree: **catalog/ledger durable state > vault record headers >
MCP structured errors > exec JSONL tool results > CLI readiness surfaces > agent narrative >
Codex final message.** Note that CLI readiness surfaces rank *below* durable state in this
revision — [§4.4](#44-layer-4-the-readiness-reporting-artifact) is why.

### 4.1 Layer 1: passphrase mode makes unlock process-scoped

`installation-state.json` records `vault_mode: passphrase`, created `2026-07-25T17:36Z`. Yoetz has
two vault modes (`src/yoetz/ports/keys.py`, `src/yoetz/service/vault.py`):

| Mode | Boot behavior | Where the key lives |
| --- | --- | --- |
| `OS_KEYRING` | `_load_keyring_ready()` (`vault.py:676`) loads the IVK from the platform store and reaches `READY` without a human | OS keyring, entry `yoetz.vault-root.v1` |
| `PASSPHRASE` | Cannot self-unlock from the envelope alone. The IVK is AES-KW-unwrapped with an Argon2id key derived from a passphrase (`installation-state.json:root_envelope_base64`, `memory_kib: 262144`, `time_cost: 3`) | Nowhere at rest — only in the unlocked service's memory |

Consequence: **in passphrase mode, an unlock is an attribute of one process, not of the
installation.** Kill the process and the human's authorization evaporates. There is no durable
record that the owner ever consented.

### 4.2 Layer 2: the auto-unlock regression (the real story)

Passphrase mode is *not supposed* to mean "a human every time." There is a bridge:
`AutoUnlockPassphraseStore` (`src/yoetz/adapters/keys/os_keyring.py:186`) stores one **generated**
48-byte passphrase in the platform credential store under service `yoetz.auto-unlock.v1`, account
`bundle-<sha256(abspath(bundle_root))>`. The service reads it at boot:

```python
# src/yoetz/service/daemon.py:1640-1651
auto_passphrase = AutoUnlockPassphraseStore(paths.bundle).load()
if auto_passphrase is not None:
    try:
        handle = secret_memory.capture(SecretPurpose.VAULT_UNLOCK, auto_passphrase)
        await vault.unlock(handle)
    except Exception:
        # A missing, locked, stale, or mismatched platform credential never prevents
        # the service from starting in its ordinary locked state.
        pass
```

**The write side is unreachable from any product code path.** `load_or_create()` — the only method
that provisions the entry — is referenced exactly once in the repository outside its own
definition, and that reference is a unit test using a fake backend
(`tests/integration/objects/test_key_backends.py:67`, `_AtomicBackend`).

Git archaeology (`git log -S'load_or_create' -- src/`):

| Commit | Local time | Effect |
| --- | --- | --- |
| `fed3169` "feat: persist provider setup and auto-start service" | 2026-07-22 16:21 | **Added** `load_or_create()` in `cli/setup.py` |
| `eb6bfdb` "fix: close r2 dogfood product gaps…" | 2026-07-24 17:17 | **Removed** |
| `13b0b30` "fix: repair receipt projection and semantic-mode selection (r2 postmortem)" | 2026-07-24 21:31 | **Restored** |
| `2c049e0` "feat: enhance provider setup with reviewed presets and improved CLI options" | 2026-07-24 22:29 | **Removed again** — current state on `main` |

The final removal was collateral damage, not a security decision. The deleted branch was gated on a
parameter the presets refactor eliminated:

```diff
-                if api_key is not None:
-                    auto_store = AutoUnlockPassphraseStore(bundle_root(...))
-                    try:
-                        auto_passphrase = auto_store.load_or_create()
-                    except OSKeyringError:
-                        typer.echo("Platform credential store unavailable; choose a vault passphrase")
-                        await initialize_passphrase_vault()
-                    else:
-                        typer.echo("Secure vault setup (platform credential store auto-unlock)")
-                        await initialize_passphrase_vault(bytearray(auto_passphrase))
-                else:
-                    typer.echo("Secure vault setup (hidden local-terminal input)")
-                    await initialize_passphrase_vault()
+                typer.echo("Secure vault setup (hidden local-terminal input)")
+                await initialize_passphrase_vault()
```

`api_key` went away with the presets rework; the auto-unlock branch went with it. Nothing failed —
no test covered it.

**Why this machine still has a keychain entry, and why it does not help.** The entry exists and its
account digest matches this bundle exactly:

```text
svce = "yoetz.auto-unlock.v1"
acct = "bundle-02826ab554c535f8eaf74564c6121e59cefe52f9d8b6edddd5c49cf8b2280ba5"
cdat = 20260722121217Z
```

`sha256(abspath("/Users/shayb/Library/Application Support/yoetz"))` =
`02826ab554c535f8eaf74564c6121e59cefe52f9d8b6edddd5c49cf8b2280ba5`. Confirmed match. But the entry
was written on **2026-07-22**, while this installation's vault was initialized fresh on
**2026-07-25T17:36Z** with a human-typed passphrase. The stored passphrase belongs to a destroyed
installation, so it cannot unwrap the current `root_envelope`. `daemon.py:1645-1648` catches the
failure and **silently** proceeds to `LOCKED` — exactly the "stale or mismatched" case the comment
anticipates, with no log line, no status field, and no operator signal that auto-unlock was
attempted at all.

So the machine is in the worst of both worlds: a keychain secret that looks provisioned, a service
that tries it on every boot, a failure that is invisible, and no product command that can repair it.

### 4.3 Layer 3: process churn and why nothing was running at 18:51Z

| Evidence | Reading |
| --- | --- |
| `unlock-throttle.json`: `consecutive_failures: 0`, `last_writer_instance_id: svc_76c0f0ac…`, mtime `18:44Z` | A service was alive **and successfully unlocked** 7 minutes before launch |
| `catalog.sqlite3` mtime `18:45Z` | That instance was doing real work |
| Preflight `18:51Z`: `service_unavailable` | It was gone |
| `IDLE_STOP_SECONDS = 1800`, `_DEFAULT_IDLE_SECONDS = 900` (`service/lifecycle.py:43-44`) | Neither timer can fire in 6 minutes → **externally terminated** (foreground `yoetz service run` ended, terminal closed, or explicit stop) |
| `service-generation.json`: `generation: 26`, installation age 76 minutes; generation advances once per process that wins the singleton (`lifecycle.py:292`) | ~26 service processes in 76 minutes — extreme churn, consistent with hand-supervised foreground runs |

There is no supervisor integration. The CLI's own message — *"run `yoetz service run` under your
selected user supervisor"* — hands the operator a problem the product does not solve. On macOS the
natural answer is a `launchd` LaunchAgent, and Yoetz ships nothing to install one.

**Two independent failure modes therefore compound:** the process does not survive (no supervisor),
and even if it did, an unlock does not survive it (no auto-unlock provisioning). Fixing one without
the other still leaves a system that stops working while you are not looking.

### 4.4 Layer 4: the readiness reporting artifact

This is the layer that corrupted Revision 1's conclusions, and it is a pure product defect with no
security tradeoff attached.

`src/yoetz/cli/provider_status.py:124-149` reads the policy and credential capability **only** when
the service is `ready`:

```python
client = await connect_service(ControlClientKind.CLI)
status = await client.service_status()
service_state = status.state.value
if status.state.value == "ready":
    credential_connected = "external_provider" in status.capabilities
    effective = await client.privacy_get_effective(machine_scope_request())
    llm_inference_enabled = _channel_enabled(policy_map, "llm_inference")
else:
    credential_connected = None
```

Then, at `:154-181`, both `None` values become blockers with prescriptive next commands:

```python
if credential_connected is not True:
    blockers.append({"condition": "provider_credential",
                     "state": "unknown" if credential_connected is None else "not_connected",
                     "next_command": "yoetz provider credential set"})
if llm_inference_enabled is not True:
    blockers.append({"condition": "llm_inference_channel",
                     "state": "unknown" if llm_inference_enabled is None else "disabled",
                     "next_command": "yoetz privacy setup"})
```

Three distinct defects live in those twenty lines:

1. **`unknown` is treated as `false`.** `semantic_ready` requires `is True`, so "I could not look"
   collapses into "you have not done it."
2. **`unknown` still emits a prescriptive `next_command`.** The operator is told to redo two human
   ceremonies that durable state shows were already completed. Following that advice on a locked
   service would fail (both ceremonies need a ready service), or on an unlocked one would
   needlessly re-enter an API key and re-run a widen ceremony.
3. **The one true blocker is never emitted.** There is no `service_unlocked` condition and no
   `yoetz service unlock` in `next_commands`, at any service state. `service_state` is reported as
   a bare field that no blocker references.

Both then and now, the surface says:

```json
{"blockers":[{"condition":"provider_credential","state":"unknown","next_command":"yoetz provider credential set"},
             {"condition":"llm_inference_channel","state":"unknown","next_command":"yoetz privacy setup"}],
 "semantic_ready":false,"service_state":"locked"}
```

while durable state says `llm_inference` is enabled and bound, and a `provider_credential` record
exists. **The readiness tool was the primary source of this postmortem's two false claims.** Any
real user hitting a service restart gets the same misdirection.

A related mislabel compounds it: `daemon.py:956-958`

```python
def _locked_reason(self) -> str:
    mode = getattr(self._composition.vault.mode, "value", self._composition.vault.mode)
    return "vault_uninitialized" if mode == "uninitialized" else "keyring_locked"
```

A passphrase-mode vault that merely needs its passphrase reports `state_reason: keyring_locked`,
sending the operator to the macOS Keychain. Revision 1 quoted it verbatim as though a keyring were
involved.

### 4.5 Layer 5: why the agent could not recover

| Attempted recovery | Outcome | Mechanism |
| --- | --- | --- |
| MCP `start` | `VAULT_LOCKED`, `retryable: no`, `err_0ef0a206…` | `daemon.py:683` — every workflow method routes through `_dispatch_ready`, which raises `ControlError("vault_locked")` when the ready application is absent; mapped to public `VAULT_LOCKED` at `mcp/server.py:243` |
| MCP `status` with `session_id: null`, `writer_id: null` | `INVALID_REQUEST`, fields `/session_id`, `/writer_id`, `invalid_type_or_value` | `status-request-1.0.0.schema.json` lists both in `required`, neither nullable |
| MCP `status` with those fields omitted | `INVALID_REQUEST`, same fields, `missing` | Same schema |
| `yoetz service unlock` from the shell (never attempted) | Would have failed `tty_required` | `cli/unlock.py:173-192` requires `/dev/tty`, `isatty(0)` **and** `isatty(2)`, and a device match. Codex exec had stdin redirected from `prompt.txt` |

Three observations worth separating:

- **The `VAULT_LOCKED` gate is correct.** Task ledgers are vault-encrypted; no vault means no
  session can exist. This is not the thing to fix.
- **The retryability flip is correct.** Internally `retryable=True` (retry after unlock), publicly
  `retryable: no` (do not retry without human action).
- **The absence of a diagnostic surface is not correct.** Codex was not thrashing — it was probing
  for a health check that does not exist. Note it got the hard part right: `limit` is a *string* in
  that schema (`pattern: ^([1-9]|[1-9][0-9]|100)$`) and it sent `"10"`. The model understood the
  protocol; the protocol had nowhere for it to go. Six tools, all session-scoped, none able to
  answer "why can't I start?"

### 4.6 What remains genuinely unknown

Honest limits of this forensic pass, all resolvable only by unlocking:

1. **Which provider the vault credential is bound to.** `provider_credential_connected` is computed
   at activation by matching the record's stored provider against config
   (`application/service.py:491-513`); the binding is inside the AES-GCM payload. One record exists
   and config is Fireworks, so the match is likely but unproven.
2. **Whether the stored key is real.** `plaintext_size: 25` is short for a Fireworks API key. It may
   be a placeholder from setup testing.
3. **Whether `semantic_ready` would actually flip `true`.** Highly likely given the policy and the
   record, but conditional on (1) and (2).
4. **How each of the 26 service processes ended.** Only the last transition is directly evidenced.

### 4.7 Layer map (revised)

| Layer | What it proves | This run |
| --- | --- | --- |
| **A. Install / CLI binary** | Packaged wheel, `yoetz --version`, MCP entrypoint | **Pass** — 0.1.0, digest above |
| **B. Harness registration** | Codex can spawn `yoetz mcp serve` as stdio MCP | **Pass** — enabled, `yoetz_owned` |
| **C. MCP process liveness** | Server up for the session | **Pass** — PID 51583 observed during run |
| **D1. Service process durability** | A service exists when a client arrives | **Fail** — none at preflight; 26 generations in 76 min; no supervisor integration |
| **D2. Unlock durability** | The owner's unlock survives a restart | **Fail** — passphrase mode + auto-unlock provisioning regressed out (`2c049e0`) |
| **D3. Locked-state diagnosis** | Operator can tell *why* it is locked and what to do | **Fail** — `keyring_locked` mislabel; no `service_unlock` blocker; silent auto-unlock failure |
| **E. Cooperative session** | `start` → session_id + writer_id + task route | **Fail** — no start, no routes |
| **F. Work ledger path** | `publish_work` / frontier / claims | **Not reached** |
| **G. Deterministic check** | Coverage / obligations without external LLM | **Not reached** |
| **H. Semantic readiness (structural)** | Endpoint + credential + privacy channel + semantic mode | **Reported false; durable state says probably true** — see §4.4 |
| **I. Semantic dispatch (runtime)** | Non-zero `semantic_jobs` / `semantic_attempts` | **None** — no ledger |
| **J. Live provider smoke** | Authorized outbound + provider response + receipt provenance | **Out of scope** |

A–C worked. **D1/D2/D3 are the product failure.** E–J were never reachable.

---

## 5. Design intent: closed by default, open once authorized

### 5.1 The principle

The intent for Yoetz, stated plainly:

> Nothing leaves this machine to anyone who is not the user or the user's agent. That is the
> guarantee. **But once the user has authorized something, it works** — and it keeps working
> without asking again. Closed by default, open by consent, and consent is durable.

Current behavior violates the second half. It is not *more* private for a locked vault to block a
task the owner already authorized — it is the same privacy posture with worse usability, plus a
readiness surface that lies about why.

### 5.2 Separating the two things "locked" protects

It matters enormously *which* guarantee the vault lock is carrying, because the improvement program
depends on it.

| Guarantee | Enforced by | Depends on vault lock? |
| --- | --- | --- |
| No outbound data without an enabled channel | privacy policy `channel_policies[].enabled` | **No** — policy is durable in the catalog |
| Outbound only to the bound provider/endpoint/model | `provider_binding` in the channel policy | **No** |
| Only allowed categories/data classes leave | `allowed_categories`, `allowed_data_classes` | **No** |
| Authorizations expire | `authorization_ttl_seconds: 300` | **No** |
| Every disclosure is auditable | `privacy_audit_records` (20 rows present) | **No** |
| Widening requires a human | proposal → `human_expansion` transition with `authority_commitment` | **No** — ceremony, not lock |
| Secrets never traverse MCP | `mcp/server.py` error text + no secret-bearing tool args | **No** |
| The API key is unreadable on a powered-off stolen disk | AES-GCM records + Argon2id-wrapped IVK | **Yes** |
| The API key is unreadable by other processes running as this user | — | **Weakly, at best** |

**Eight of the nine guarantees that constitute "nothing leaves without authorization" do not depend
on the vault being locked.** The lock buys at-rest confidentiality (real, worth keeping) and a thin
amount of live process isolation (mostly illusory: anything running as the user can attach to the
service socket, read process memory, or replace `~/.local/bin/yoetz`).

### 5.3 The honest tradeoff of auto-unlock

If auto-unlock provisioning is restored, this is what changes:

- **Preserved:** at-rest confidentiality. The generated passphrase lives in the login keychain,
  encrypted under the account password and unavailable on a powered-off machine. A stolen Mac still
  needs the login password.
- **Preserved in full:** every egress guarantee in §5.2. Auto-unlock does not enable a single byte
  of outbound traffic — the `llm_inference` channel gate, provider binding, category allowlists,
  TTLs, and audit records are untouched.
- **Given up:** the scenario "an attacker already executing code as this user, while logged in,
  cannot read the provider API key." Note this was never really protected — the same attacker could
  keylog the passphrase prompt, read the unlocked service's memory, or shim the CLI. macOS keychain
  ACLs cannot meaningfully bind an entry to an interpreted Python process.
- **Comparable posture:** `gh auth`, `aws` credentials, `npm` tokens, Docker config. Standard for
  developer tooling, and strictly better than any of them because Yoetz still gates egress by
  policy.

**Recommendation:** auto-unlock on by default for desktop installs, with a documented, tested
opt-out (`vault.auto_unlock = false` in `config.toml`) for anyone who wants the ceremony. Keep the
egress ceremonies exactly as strict as they are today. Never relax §5.2.

### 5.4 Where the implementation violates the principle

| # | Violation | Current behavior | Should be |
| --- | --- | --- | --- |
| V1 | Consent is not durable | Unlock dies with the process | Owner unlocks once; it survives restarts until revoked |
| V2 | Consent is not durable across idleness | `activate_ready_application` is reachable only from boot auto-unlock (`daemon.py:1644`) or a human ceremony (`unlock.py:822`); after a 900s idle relock only a human can restore READY | Idle relock re-locks *memory*, then transparently re-unlocks from the provisioned platform secret on the next authorized request |
| V3 | "I can't see it" reads as "you didn't do it" | `unknown` → blocker → `semantic_ready: false` | `unknown` is reported as unknown and never generates a remediation command |
| V4 | The real blocker is hidden | No `service_unlocked` condition anywhere in `provider_status.py` | First blocker, with `yoetz service unlock` |
| V5 | Silent failure of the one automatic recovery path | `daemon.py:1645-1648` swallows every auto-unlock exception | Structured reason surfaced in `service_status` and logged |
| V6 | Diagnosis requires a session | All six MCP tools are session-scoped | A session-free structural health tool that returns no secrets |
| V7 | Wrong remediation vocabulary | `keyring_locked` for passphrase vaults | `passphrase_required` / `auto_unlock_stale` / `keyring_locked`, distinctly |
| V8 | No supervisor story | "run under your selected user supervisor" | `yoetz service install` writes and loads a LaunchAgent |
| V9 | No repair path for a stale platform secret | Nothing in the CLI can rewrite the `yoetz.auto-unlock.v1` entry | `yoetz service auto-unlock enable/disable/repair` |

---

## 6. Improvement program

Ordered by leverage. Each item names the failure it removes, the files to touch, and the acceptance
test that must exist so it cannot regress a third time.

### P0-1 — Restore auto-unlock provisioning and make it a first-class, tested feature

**Removes:** V1, V9, and the root cause of this run.
**Evidence:** §4.2.

Work:

1. Reinstate provisioning in `cli/setup.py::_interactive_provider_setup`, **not** gated on a removed
   parameter. On the `vault_mode == "uninitialized"` branch: try `load_or_create()`; on
   `OSKeyringError` fall back to the typed-passphrase ceremony with a clear message.
2. Add `yoetz service auto-unlock {status,enable,disable,repair}`:
   - `status` — reports provisioned / absent / stale / backend-unsupported. No secret output.
   - `enable` — provisions the entry and re-wraps the vault root to the generated passphrase (TTY
     ceremony required once, since it changes the root envelope).
   - `repair` — the case this machine is in: entry present, does not unwrap. Re-derive after a TTY
     confirmation.
   - `disable` — delete the entry; vault reverts to typed-passphrase only.
3. Detect and report the stale case explicitly at boot instead of swallowing it (see P0-3).

Acceptance:

- Fresh install with an approved backend reaches `state: ready` after a service restart **with no
  human interaction**.
- With the entry deleted, the service starts `locked` with `state_reason: passphrase_required`.
- With a deliberately corrupted entry, it starts `locked` with
  `state_reason: auto_unlock_stale` and `yoetz service auto-unlock repair` fixes it.
- A subprocess test asserts `load_or_create` is reachable from the wizard — the missing test that
  let `2c049e0` through.

Risk: low. Restores shipped behavior behind an explicit, documented default.

### P0-2 — Make `provider status` tell the truth

**Removes:** V3, V4. **Evidence:** §4.4. This is the highest-value smallest diff in the program.

Work in `src/yoetz/cli/provider_status.py:154-181`:

1. Emit a `service_unlocked` blocker **first** whenever `service_state != "ready"`, with
   `next_command: "yoetz service unlock"` (or `yoetz service run` when the state is
   `service_unavailable`, or `yoetz service auto-unlock repair` when the reason is
   `auto_unlock_stale`).
2. When a condition's state is `unknown`, emit it **without** a `next_command`, and exclude it from
   `next_commands`. Add `"unknown means the locked service could not be read, not that the step is
   incomplete"` to `notes`.
3. Add an explicit `readiness_determinable: bool` field so callers (and harnesses) can distinguish
   "not ready" from "cannot tell."
4. Consider using on-demand connect here, or at minimum say so: `service_unavailable` should not
   look like a hard failure when `yoetz service run` fixes it.

Acceptance: with a locked service, `next_commands[0] == "yoetz service unlock"` and neither
`yoetz provider credential set` nor `yoetz privacy setup` appears. A conformance test locks the
blocker ordering and the unknown-without-command rule.

### P0-3 — Never fail silently at the one automatic recovery point

**Removes:** V5, V7. **Evidence:** §4.2, §4.4.

Work:

1. `daemon.py:1640-1651` — classify the failure instead of `pass`: `auto_unlock_absent`,
   `auto_unlock_backend_unavailable`, `auto_unlock_stale`, `auto_unlock_rejected`. Store it and log
   one structured line. Never log secret material.
2. `daemon.py:956-958` — replace the `keyring_locked` catch-all:

   ```python
   def _locked_reason(self) -> str:
       if mode == "uninitialized":
           return "vault_uninitialized"
       if mode == "os_keyring":
           return "keyring_locked"
       return self._auto_unlock_reason or "passphrase_required"
   ```

3. Add `state_reason` to the documented `service_status` surface with the full enumeration, and add
   the new reasons to `docs/INTERFACES.md`.

Acceptance: every locked start emits exactly one structured reason; a test asserts a passphrase-mode
vault with no keychain entry never reports `keyring_locked`.

### P1-1 — Transparent re-unlock after idle relock

**Removes:** V2. **Evidence:** §4.7 D2, `lifecycle.py:451-480`.

Today `run_idle_monitor` relocks after 900s idle and only a human can restore READY. With
auto-unlock provisioned, that is pure friction: the secret needed to re-unlock is already on the
machine and the owner already consented.

Work: on a workflow request arriving in `LOCKED` with auto-unlock provisioned, attempt one
transparent re-unlock (re-read the platform secret, `UNLOCKING` → `READY`, then dispatch). Bound it:
one attempt per request, respect the unlock throttle, record it in the service audit stream.
Alternative if that is too invasive for now: skip idle *relock* when auto-unlock is provisioned and
keep only idle *stop* — the same security posture, since a restart would auto-unlock anyway.

Acceptance: after a forced relock, an MCP `start` succeeds without human interaction and the audit
stream shows one `auto_unlock` event.

### P1-2 — Session-free structural health tool

**Removes:** V6. **Evidence:** §4.5.

Add a seventh MCP tool (`health`) or a session-free `status` mode returning **structural facts
only**: `service_state`, `state_reason`, `vault_mode`, `semantic_ready`, `readiness_determinable`,
the blocker list from P0-2, and the next human command. No task data, no secrets, no policy
contents beyond channel-enabled booleans.

This is what Codex was reaching for with two malformed `status` calls. It converts a dead end into
an actionable error and directly raises the "Yoetz tooling effect" ceiling: an agent that can read
"locked; the owner must run `yoetz service unlock`" can report a precise blocker to the user instead
of a correlation ID.

Acceptance: with a locked vault, `health` returns `ok: true` with `service_state: "locked"` and a
next command. A privacy test asserts no task-derived or vault-derived field can appear in its
projection.

### P1-3 — Ship a supervisor

**Removes:** V8. **Evidence:** §4.3.

`yoetz service install` / `uninstall` / `status`: write a `launchd` LaunchAgent on macOS
(`KeepAlive`, `RunAtLoad`, owner-only plist, stderr to the existing log path), systemd `--user` unit
on Linux. Then `service_unavailable` becomes rare instead of routine, and the 26-generations-per-76
-minutes churn disappears.

Acceptance: after `yoetz service install`, `launchctl kill` is followed by an automatic restart, and
with P0-1 in place the restarted service reaches `ready` unattended.

### P2-1 — Make the readiness contract legible end to end

- `docs/usage/providers.md`: a "why is it locked / how do I make it stay unlocked" section covering
  the three lock reasons, auto-unlock, and the supervisor.
- `docs/INTERFACES.md`: `state_reason` enumeration, `readiness_determinable`, blocker ordering.
- An ADR for the auto-unlock default. This *is* a privacy/egress-adjacent design gate, so it needs
  the ceremony — but the ADR should record the §5.2 analysis: the lock is not what carries the
  egress guarantee, and the default should optimize for the product working.

### P2-2 — Stop leaving stale platform secrets behind

Installation teardown / re-initialization must delete or invalidate `yoetz.auto-unlock.v1` for that
bundle. Bind the entry to `installation_id` in addition to the bundle-path digest, so a
re-initialized installation cannot silently inherit a dead secret — which is precisely what happened
here.

### P2-3 — Harness protocol v2 (see also §8)

Move the readiness gate out of prose and into a script that *aborts*, so no future run can be
labeled a Yoetz-effect experiment without a proven cooperative path.

### Program summary

| ID | Change | Removes | Effort | Risk |
| --- | --- | --- | --- | --- |
| P0-1 | Restore + productize auto-unlock provisioning | V1, V9 | M | Low |
| P0-2 | Truthful `provider status` blockers | V3, V4 | S | Low |
| P0-3 | Structured lock reasons, no silent swallow | V5, V7 | S | Low |
| P1-1 | Transparent re-unlock after idle relock | V2 | M | Medium |
| P1-2 | Session-free health tool | V6 | M | Low |
| P1-3 | `yoetz service install` supervisor | V8 | M | Low |
| P2-1 | Docs + ADR for the auto-unlock default | — | S | Low |
| P2-2 | Bind platform secret to installation id | — | S | Low |
| P2-3 | Harness preflight gate | — | S | Low |

P0-2 and P0-3 alone would have prevented both of Revision 1's false conclusions. P0-1 alone would
have made this run succeed.

---

## 7. Regression tests to add

The regression that caused this run was possible because nothing tested it. Minimum coverage:

| Test | Asserts | Prevents |
| --- | --- | --- |
| `tests/subprocess/test_setup_wizard_cli.py` — auto-unlock provisioning | The wizard's uninitialized branch reaches `load_or_create()` with an approved fake backend | Re-deleting the write side (`2c049e0` class of bug) |
| Service restart integration | Provisioned install → restart → `state: ready`, zero prompts | V1 |
| Stale-entry integration | Corrupted entry → `state: locked`, `state_reason: auto_unlock_stale`, `repair` fixes it | This machine's exact condition |
| `provider_status` conformance | Locked service → first blocker is `service_unlocked`; no `next_command` on `unknown` conditions | V3, V4 |
| `_locked_reason` unit | Passphrase mode without keychain entry never returns `keyring_locked` | V7 |
| Idle-relock recovery | Forced relock → next authorized request succeeds unattended | V2 |
| `health` tool privacy | No task-derived or vault-derived field can project | V6 regressions |
| Dead-code guard | Public adapter methods called only from tests are flagged in review | The whole class of "read side survives, write side dies" |

That last one is worth its own note: `load_or_create` sat as an untested-in-production public method
for a day and no signal fired. A lint or review checklist entry for "adapter method referenced only
by tests" would have caught it.

---

## 8. Harness protocol v2

Revision 1's P0 was right but advisory. Make it executable and blocking.

```bash
#!/usr/bin/env bash
# preflight-cooperative.sh — abort unless Yoetz can actually accept work.
set -euo pipefail

yoetz service status --json | tee "$RUN/preflight-service.json"
state=$(jq -r .state < "$RUN/preflight-service.json")
[ "$state" = "ready" ] || { echo "ABORT: service state=$state (unlock first)"; exit 78; }

yoetz provider status --json | tee "$RUN/preflight-provider.json"
jq -e '.readiness_determinable == true' < "$RUN/preflight-provider.json" \
  || { echo "ABORT: readiness undeterminable"; exit 78; }

# Dry cooperative probe: a real start must succeed, and must leave a route.
yoetz mcp dry-start --json | tee "$RUN/preflight-drystart.json"   # (tool to add)
routes=$(sqlite3 "$DATA/catalog.sqlite3" 'select count(*) from task_routes;')
[ "$routes" -ge 1 ] || { echo "ABORT: cooperative path unproven"; exit 78; }

# Only if live semantics is in scope:
jq -e '.semantic_ready == true' < "$RUN/preflight-provider.json" \
  || echo "LABEL: wiring/honesty only — no live semantics"
```

Additional harness changes:

| Priority | Change | Required evidence |
| --- | --- | --- |
| **P0** | Abort (exit 78) unless `service.state == ready` **and** a dry `start` leaves `task_routes ≥ 1`; otherwise relabel the run "cooperative path N/A" | `preflight-*.json` in the run dir before Codex launches |
| **P0** | Record `state_reason` and auto-unlock status in `meta.txt`, not just `service_state` | `meta.txt` |
| **P1** | Capture MCP server stderr next to the exec JSONL so correlation IDs resolve offline | `mcp-server.log` |
| **P1** | Snapshot catalog **and** ledger at end (copy first) | ledger only if start succeeded |
| **P1** | Separate **attempt rate** from **effect rate** in writeups | "Used Yoetz" ≠ "Yoetz changed outcomes" |
| **P2** | Decode `privacy_policy_versions` in every writeup rather than trusting `provider status` | Durable state outranks CLI surfaces — this run is the proof |
| **P2** | Keep the service alive for the whole run (P1-3 supervisor) and assert liveness at exit | process check in `meta-finished` |

---

## 9. Evidence appendix

### 9.1 MCP tool transcript (exact)

| # | Tool | Result | Correlation | Meaning |
| --- | --- | --- | --- | --- |
| 1 | `start` | `VAULT_LOCKED` (`retryable: no`) | `err_0ef0a206-7901-4c58-8609-754c07245d99` | No session; no task |
| 2 | `status` | `INVALID_REQUEST` fields `/session_id`, `/writer_id`, reasons `invalid_type_or_value` | `err_67167f1b-a266-4691-9240-15c469b2ebe4` | Called with JSON `null`s |
| 3 | `status` | `INVALID_REQUEST` same fields, reasons `missing` | `err_2199f28a-74eb-4c66-89f3-72b026a95da8` | Called without those fields |

**Not called:** `publish_work`, `check`, `receipt`, `respond`.

`start` arguments (non-secret): `mode=create_or_attach`, task title about OpenRouter semantic
binding, actor `agt_codex`, client `codex_cli` / `cooperative_mcp`, `requested_view=compact`,
well-formed `request_id` (`req_<uuid>` shape). `status` arguments were schema-correct apart from the
required session identity, including the string-encoded `limit: "10"`.

`start` error text (verbatim):

> The local service vault is locked or uninitialized. Unlock from a local terminal
> (`yoetz service unlock`). If the vault is still uninitialized and no user-owned TTY is available,
> prepare `vault_initialize` via `yoetz consent catalog` / `prepare` (ADR-015); never send secrets
> over MCP.

Correct, non-leaky, and unactionable in-band. P1-2 is what makes it actionable.

### 9.2 Timeline (UTC; local = UTC+3)

```text
17:34   service.stderr.jsonl: "internal_error: the command could not be completed"
17:36   installation ins_95c1f9ba… created; vault initialized, mode=passphrase
        privacy policy v1 seeded (local_only, all channels disabled)
17:37   vault record vrec_3d6ada66… written, record_kind=provider_credential
18:04   privacy proposal ppr_123d7d7b… (pending, later expired)
18:06   privacy proposal ppr_7a8cb09c… (pending, later expired)
18:07:44 privacy proposal ppr_206fbc15… COMMITTED, human_expansion
         → policy v2 current: llm_inference enabled, bound fireworks-responses/minimax-m3
18:44   unlock-throttle written by svc_76c0f0ac…, consecutive_failures=0  ← service unlocked
18:45   catalog.sqlite3 written                                          ← service working
18:5x   service gone (externally terminated; no timer explains 6 minutes)
18:51:44 codex-testing exec starts
18:51:5x MCP start → connect_service_on_demand → spawns svc_4c795f57 (generation 26)
         boot: AutoUnlockPassphraseStore.load() returns the STALE 2026-07-22 secret
               → vault.unlock fails → exception swallowed → LOCKED
         start → VAULT_LOCKED
18:52-18:56 GitHub search, code trace, 2× status INVALID_REQUEST, 4-file edit, pytest/ruff/pyright
18:56:42 exec exits 0
18:59   agent3 probes: state=locked, state_reason=keyring_locked, generation 26
19:17+  same instance still alive and locked (verified live during this revision)
```

The `service_unavailable` → `locked` transition Revision 1 called "surface variance" is visible
here: Codex's own `start` created the service that later reported `locked`.

### 9.3 Durable state at run time

| Table / metric | Value |
| --- | --- |
| `task_routes` | **0** |
| `start_operations` | **0** |
| `retained_task_routes` | 0 |
| `maintenance_operations` | 0 |
| `privacy_policy_versions` | 2 (v2 current, `human_expansion`) |
| `privacy_policy_transitions` | 3 (1 committed, 2 expired-pending) |
| `privacy_audit_records` | 20 |
| `tasks/*/ledger.sqlite3` | **absent** |
| `semantic_jobs` / `semantic_attempts` | n/a (no ledger) |
| `semantic_dispatch_happened` | **false** |
| Vault records | `provider_credential` (25 B plaintext), `vault_sentinel` (32 B) |
| Keychain `yoetz.auto-unlock.v1` | present, acct matches bundle digest, **created 2026-07-22** (stale) |

Cross-check:

| Claim channel | Says | Durable state | Agreement? |
| --- | --- | --- | --- |
| MCP `start` | failed `VAULT_LOCKED` | no routes / no start ops | **Yes** |
| MCP `status` | invalid args | no session to report | **Yes** |
| Codex final message | start failed; no receipt | no ledger | **Yes** |
| CLI `provider status` | credential + `llm_inference` are blockers | policy v2 enabled + credential record present | **No — the CLI is wrong** |
| r4 P0 pattern | committed write as `INTERNAL_ERROR` | n/a — no write | **Not reproduced** |

### 9.4 OpenRouter as a product option (already on packaged main)

| Surface | Fact |
| --- | --- |
| Preset id | `openrouter` |
| Endpoint profile | `openrouter-openai-chat-completions` `1.0.0` |
| Capability | `openrouter-openai-chat-completions-1` |
| Wire | OpenAI-compatible **Chat Completions** |
| Host / path | `openrouter.ai` + `/api/v1` → chat completions URL |
| Default model (preset helper) | `openai/gpt-5.2` |
| Factory table | `CHAT_COMPLETIONS_ENDPOINT_PROFILES` entry with `provider_enforced` structured-output enforcement |
| CLI | menu option 5; `yoetz provider endpoint --provider openrouter --model …` |
| Docs / ADR | ADR-006; `docs/usage/providers.md` lists openrouter among reviewed presets |
| Live evidence (E-007) | Still **not** claimed — none of r1–r5 produced authorized live smoke |

Operator path to structural `semantic_ready` with OpenRouter, corrected for §4.4 (steps 3–4 were
already satisfied for Fireworks on this machine; they are listed because switching the binding to
OpenRouter requires an OpenRouter credential and a re-bound channel):

1. Ensure the service is running and **unlocked** — `yoetz service unlock`, or after P0-1, nothing
   at all.
2. Bind: `yoetz provider endpoint --provider openrouter --model <id>`.
3. Credential: `yoetz provider credential set` — must be an **openrouter** key; a credential for a
   different provider does not count.
4. Privacy: `yoetz privacy setup` enabling `llm_inference` **with `provider_binding` pointing at the
   OpenRouter profile**. The existing v2 policy binds Fireworks, so a rebind is required — this is
   the one place Revision 1's step 4 survives, for a different reason than it stated.
5. Confirm: `yoetz provider status --json` → `semantic_ready: true` (structural only).
6. Only then can a `check` with `mode=semantic_required` leave `provider_not_configured` and create
   non-zero `semantic_jobs`.

Even after 1–5, **live** "working endpoint" remains gated by capability evidence and authorized
outbound (ADR-006 / E-007). Structural readiness ≠ verified OpenRouter.

### 9.5 Effect of Yoetz tooling on Codex (Agent 2 metric)

| Score | Definition | This run |
| --- | --- | --- |
| strong positive | check/receipt change claims or path | no |
| mild positive | failed tools still steer behavior | no |
| **none** | no session; honesty from non-Yoetz sources | **yes** |
| negative | tools induce over-claim or bad process | no |

**What Yoetz did influence (process only):** the prompt's active-MCP requirement made Codex attempt
`start` early (good ordering); the error text plus prompt rules kept it from putting secrets on MCP
or inventing an unlock; it reported the failure accurately.

**What Yoetz did not influence:** reusing the existing factory (code search + ADR-006 + prompt);
not opening a PR (issue #15 design gate); "not live-verified" honesty (prompt + existing docs);
the content of the 4-file polish.

**Counterfactual, revised.** Revision 1 predicted that an unlocked vault would still have yielded
`check` → `provider_not_configured`. Given §4.4, that is probably wrong: with the policy already
widened and a credential record present, an unlocked service would plausibly have reported
`semantic_ready: true` and attempted a real Fireworks semantic dispatch. **This run may have been
one unlock away from the first live semantic evidence in the r1–r5 series** — bounded by §4.6's
unknowns about the stored key.

### 9.6 Three-agent outcomes

| Agent | Verdict |
| --- | --- |
| 1 Interact | Session success; honest MCP failure report; dirty tree only |
| 2 Review | **approve** UX polish; **Yoetz influence = none** |
| 3 Yoetz health | MCP served; vault locked; no ledger; OpenRouter preset present |

### 9.7 Codex / process findings

- **Model/effort:** `gpt-5.6-luna` @ `high` as configured.
- **Intake:** found issue #15; did not open a PR without maintainer ack.
- **Scope control:** reused the factory; did not re-ship multiprovider presets.
- **Protocol competence:** correct `req_<uuid>` shape, correct string-encoded `limit` — the failed
  `status` calls were a search for a missing surface, not sloppiness.
- **Verification:** focused tests (67 + 14 claimed), Ruff, Pyright 0 — not re-run after discard.
- **Secrets:** no API keys in argv, logs, or diff.
- **Agent 2 verdict:** approve for the discarded polish; a zero-diff outcome would also have been
  valid given already-complete wiring.

### 9.8 Not product defects (unchanged from Revision 1)

| Observation | Why not a defect |
| --- | --- |
| `VAULT_LOCKED` on `start` | Correct trust boundary — ledgers are vault-encrypted |
| No task without an unlocked vault | Correct |
| No secrets over MCP | Correct (error text + agent compliance) |
| TTY requirement for unlock / credential ceremonies | Correct (ADR-015); do not relax |
| Public `retryable: no` on `VAULT_LOCKED` | Correct — retrying without human action is pointless |
| OpenRouter "already exists" | Intended; prior PRs landed presets/factory |
| Discard of the UX polish | Process choice, not a Yoetz bug |

### 9.9 Product residuals from earlier runs (not exercised here)

| ID | Notes |
| --- | --- |
| r4 P0 committed-write / `INTERNAL_ERROR` | Not exercised (no write occurred). Neither fixed nor refuted |
| Live endpoint evidence (E-007) | Still unmet for OpenRouter and peers |
| Readiness conditions for semantic dogfoods | Still required — but see §4.4 before trusting the surface that reports them |

---

## 10. Bottom line

This dogfood exercised `codex-testing` against the latest packaged Yoetz MCP registration and
produced an honest, cleanly-failing run: no ghost task, no durable side effects, no over-claim, and
a correct public error with a correlation ID. Codex behaved well.

Yoetz did not. Not because its trust boundaries are wrong — they are right, and §9.8 should not be
touched — but because **the owner had already authorized everything the run needed, and the product
threw that authorization away when a process exited.** The privacy policy was widened and bound. The
credential was in the vault. The endpoint was configured. Semantic review was enabled. And the
readiness surface reported two of those four as blockers, prescribed redoing them, and never
mentioned the one command that would have worked — because a locked service cannot read its own
state and the code treats "cannot read" as "not done."

Underneath that sits a one-day-old silent regression: the mechanism that makes an unlock durable was
shipped, removed, restored, and removed again inside 48 hours, with the read side left wired to a
write side that no longer exists.

The lesson is not "unlock before dogfooding." It is that **Yoetz's guarantee — nothing leaves this
machine except to the user and the user's agent — is carried almost entirely by the privacy policy,
the provider binding, the category allowlists, the TTLs, and the audit trail, none of which depend
on the vault being locked.** The lock is buying at-rest confidentiality, which is worth keeping, and
a live-process guarantee that was mostly illusory. Trading that thin guarantee for a system that
keeps working after a restart costs nothing that matters and buys the difference between a tool
people use and a tool people fight.

Fix P0-1, P0-2, P0-3 and the next run measures Yoetz instead of measuring the vault.

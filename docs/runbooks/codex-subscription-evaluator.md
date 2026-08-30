# Codex subscription semantic evaluator

This runbook owns the exact `codex-chatgpt-subscription@1` cell. It is an external semantic
evaluator behind the ordinary Yoetz privacy gateway, not the Codex host integration and not an
OpenAI Platform API profile.

## Exact v1 cell

| Fact | Required value |
|---|---|
| Distribution | OpenAI Codex npm `0.150.1` |
| Platform | macOS arm64 |
| Native executable SHA-256 | `a14f9a907c12c8812878b70e6b7d65f81c39ed795513e46a55817d7428c0ca6b` |
| App-server schema SHA-256 | `8cdccfc35582696d7141e7f916e0d5a664ab5b5e90b732f104284d2507f369f8` |
| Isolated config SHA-256 | `c11ecc6c60e5618ca1b988760ef643250527757a34ef2cbb9d393306236593da` |
| Capability-cell SHA-256 | `ad3e9a354ce29dd459e7549ac77db4425f6f1a41c4bc8dfd62316103c2897e28` |
| Capability profile | `codex-evaluator/0.150.1/v1` |
| Capability evidence expires | `2026-11-30T00:00:00Z` |
| Transport | app-server v2, stdio JSONL |
| Credential authority | `external_runtime_oauth` |
| Upstream-body observability | `unavailable` |

No other platform, Codex version/build, binary digest, app-server schema, config, model, or
reasoning setting inherits this cell. The exact identity digest covers the compatibility-critical
cell fields and its review/expiry dates; stale evidence fails before child launch. The release
support matrix remains authoritative about which cells have completed packaged live evidence.

## Setup and reverse operations

Use a dedicated home; never point this route at a normal Codex home or copy authentication from
one.

```text
yoetz provider codex-subscription setup \
  --executable /absolute/path/to/codex \
  --model gpt-5.6-sol \
  --reasoning-effort high

yoetz provider codex-subscription status --json
yoetz provider codex-subscription disconnect --accept
yoetz provider codex-subscription rollback
```

Setup resolves the selected npm wrapper to its exact native binary and refuses every unknown
digest. Before Codex login it shows the runtime, destination, model/reasoning selection, dedicated
home, unknown plan-specific data-use posture, privacy implication, and reverse commands. Browser
and device-code login are the only accepted methods. Disconnect asks Codex to log out and removes
the Yoetz binding only after structural confirmation; rollback never logs out or deletes the home
or installation.

## Isolation contract

Each dispatch launches one process group with the exact executable, `--strict-config`, the
digest-bound config, and repeated critical deny overrides. The environment allowlist contains only
the dedicated `CODEX_HOME`, fixed locale, fixed system `PATH`, and bounded Rust log level; API-key
and proxy variables do not cross. Analytics and OTel are off.

Before task bytes cross stdin, Yoetz initializes app-server v2, requires a ChatGPT account,
requires the exact model/reasoning cell from `model/list`, and starts an ephemeral thread in a new
empty owner-private cwd. The returned cwd, model/provider, read-only/no-network sandbox posture,
empty instruction-source list, and absence of a persisted thread path must match. The exact
approved case then enters only as `turn/start` text with the digest-bound Codex projection of the
frozen judgment schema. The exact runtime rejects JSON Schema's `uniqueItems` keyword, so that is
the only omitted provider-side constraint; Yoetz's unchanged local normalizer still enforces every
uniqueness rule before accepting a judgment. Any child tool request, tool item, unknown event,
extra agent message, invalid/truncated/refused completion, or configuration mismatch fails closed.
Prompt wording is not treated as the isolation boundary.

The cell is application confinement, not a general OS sandbox claim. Its negative controls and
exact-version behavior are part of compatibility evidence; a version that cannot prove the listed
postconditions is unsupported.

## Privacy and receipts

The same ADR-009 classifier, minimizer, never-send scanner, composed repository authority,
optional per-request approval, one-use authorization, and terminal receipt govern this route. A
strict MCP route, unapproved repository, missing login, missing model, stale binary/config, or
unsupported cell produces zero task-content disclosure.

`semantic_provenance.runtime_evidence` records only exact digests and bounded structural facts. It
never retains email, token, credential path, raw account/workspace identity, prompt, reasoning,
stderr, or event log. `disclosed_case_sha256` is the case Yoetz passed to Codex, not Codex's
upstream request. The explicit `upstream_body_observability=unavailable` field is mandatory.

Before `turn/start` acknowledgement, a transient may consume a fresh authorization and capped
retry. After acknowledgement, ambiguous transport or unverified process-group cleanup is terminal
`unavailable/outcome_unknown`; do not retry it. Success requires schema-valid output and verified
group disappearance before the terminal receipt.

## Packaged live-evidence checklist

Use an exact packaged Yoetz build and an isolated logged-in evaluator home. Record these as
separate claims:

1. two `semantic_required` checks complete with distinct semantic attempts, one-use
   authorizations, process groups, runtime evidence, and terminal privacy receipts;
2. the judgments validate against the frozen schema and any corrective finding is handled through
   the normal `respond`/`publish_work`/recheck loop;
3. another unapproved repository and a strict host route launch no child and disclose nothing;
4. logged-out home, incompatible binary, unavailable model/reasoning, modified config, hostile
   project instructions, same-name binary, API/proxy environment, and attempted tool events fail
   before disclosure or record the exact bounded post-disclosure failure;
5. disconnect and rollback leave unrelated Codex installations, homes, settings, and sessions
   byte-unchanged.

Do not call login, a model listing, unit tests, or one clean judgment proof of this checklist.

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

### Login lives once per dedicated home

Codex owns the OAuth state of the dedicated home (`auth.json`, refresh, logout). `setup` therefore
treats a sign-in as something to prove, not something to repeat (#534): after the local preflight
and `prepare_codex_home`, it runs the same structural probe as `status` — app-server
`account/read` with `refreshToken: false` followed by `model/list` — and, when Codex reports a
ChatGPT account with the exact model/reasoning cell available, writes the binding and returns
`login_reused: true` without issuing `account/login/start`. The three non-reuse edges are distinct:
a logged-out home or a home missing the exact model takes the ordinary login path, which still
fails with its existing `codex_subscription_readiness_unproven` token when the login cannot prove
the cell; a probe whose process-group cleanup is unconfirmed fails closed with that same token
*before* any further child launch, never falling through to login; and a dedicated home whose
`config.toml` differs still fails `codex_runtime_config_conflict` before any process starts. A
probe that cannot complete at all — an unreachable or unanswering app-server, an expired
capability cell — fails with its own bounded token rather than silently opening a login.
`--switch-account` (the prompt-loop "switch ChatGPT account" confirmation and the `/provider`
"Switch Codex ChatGPT account" choice) is the explicit override: it skips the probe, logs the home
out through Codex, and signs in again. Yoetz never reads, copies, or moves `auth.json`; readiness
is only what Codex answers.

The dedicated evaluator home may be reused across runs, including isolated dogfood runs, by
passing the same owner-private directory to `--codex-home`. The parity report identifies it by
its existing `codex_home_digest`. It must remain a dedicated home — never the ambient user Codex
home and never the per-run host home — and `disconnect` remains the way to log it out. Because a
reused home outlives the isolation root, its full teardown is `disconnect` followed by the
operator deleting that directory; deleting the isolation root does not remove it (ADR-026).

## Selected executable resolution

Pass one absolute selected path. The supported npm layouts are:

1. the npm wrapper whose `@openai/codex-darwin-arm64` optional package is nested below that
   wrapper's package root; and
2. an npm-prefix wrapper whose native package is hoisted beside `@openai/codex` under the same
   selected prefix.

The third supported form is the exact native `codex` executable. Resolution follows only the
selected wrapper's package root and, for a prefix install, that same prefix. It never searches
arbitrary PATH entries, unrelated prefixes, or unbounded parent directories. All forms retain the
platform, package-version, native-executable, and exact-digest checks; an executable that runs but
is not the closed capability cell returns a bounded failure token.

Setup resolves the selected wrapper to its exact native binary and refuses every unknown digest.
Before Codex login it shows the runtime, destination, model/reasoning selection, dedicated home,
unknown plan-specific data-use posture, privacy implication, and reverse commands. Browser and
device-code login are the only accepted methods. The browser window is 600 seconds; the device-code
window is 900 seconds. Cancellation and timeout use bounded process-group termination, pipe close,
and task cleanup before returning one terminal diagnostic.

Codex 0.150.1 emits `remoteControl/status/changed` immediately after initialization, so either
login method may receive that notification while `account/login/start` is outstanding. The login
waiter follows the same reviewed pre-disclosure method allowlist as the evaluator: accepted
structural notifications are demultiplexed and discarded unread, with the remote-control and rate
limit shapes validated; warnings remain fail-closed. `account/login/completed` remains the only
terminal login event and must carry the exact `loginId` with `success: true`. Unknown, tool, or
otherwise unallowlisted notifications still fail closed. This mirrors the official SDK's separate
login waiter/global-notification routing without introducing a broad ignore or carrying notification
payload content across the adapter.

No partial Yoetz binding is written. A timeout, denial, malformed completion, process exit,
cancellation, or later configuration-write failure leaves a new or replacement Yoetz binding
uncommitted. If Codex completed its own login before the failure, its OAuth state may remain in the
dedicated home because Codex owns authentication, refresh, `auth.json`, and logout. Use
`disconnect` to request Codex logout and then remove the Yoetz binding; use `rollback` to remove
only the Yoetz binding while preserving the home and installation. Guided setup, the prompt-loop
menu, and `/provider` can log out the dedicated home first when switching accounts. CLI, menu, and
`/provider` recompose the local service after setup, disconnect, or rollback so a running daemon
cannot keep dispatching the previous cell.

Service READY composition does not spawn a Codex app-server to prove login. The READY credential
fact is the exact binding, executable digest, isolated config, and dedicated home. `account/read`
and `model/list` run inside the same `evaluate()` child that will disclose the case, or from
`yoetz provider codex-subscription status`.

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
invalid/truncated/refused completion, or configuration mismatch fails closed. Codex tags each
agent message with a phase: `commentary` messages are interim narration and are discarded
unread; only `final_answer` messages are judgment candidates, and exactly one must remain
(untagged messages from legacy models fall back to the same one-message rule). Informational
notifications — thread naming, moderation metadata, safety buffering, deprecation and
configuration notices, queue and compaction state, plan updates — and the model's own `plan` and
`contextCompaction` items are validated for method/type only and discarded; none of their bodies
is retained. A `model/rerouted` notice ends the turn as `refused`, because the bound model did not
produce the answer. The post-acknowledgement event budget is 4096 notifications, each bounded to
1 MiB, so a content-rich streamed judgment is not mistaken for an unbounded stream. Prompt wording
is not treated as the isolation boundary.

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

Structural readiness, privacy authority, and live dispatch remain separate claims. `status` can show
the exact binding, dedicated-home readiness, account mode, or model availability without authorizing
disclosure. The machine privacy ceiling and exact repository grant independently permit or refuse a
case. Only an admitted `evaluate()` child with a semantic attempt and terminal receipt proves that
task bytes were dispatched; login success, model listing, or `semantic_ready: true` alone never does.

## Diagnosing a failed attempt

`semantic_status` / `semantic_reason` stay the closed public pair. The exact stage is
`semantic_provenance.runtime_evidence.failure_stage` in the receipt JSON, and the service writes
the same token as an owner-only diagnostic line (`semantic_composition` /
`semantic_provider_attempt_invalid`). Stages are registered literals, never provider text:

| Stage | Meaning | Retry posture |
|---|---|---|
| `capability_evidence_stale`, `launch_failed`, `initialize_invalid`, `login_required`, `model_unavailable`, `thread_invalid`, `predisclosure_event_forbidden` | Failed before the case crossed stdin. | Ordinary pre-disclosure transient/unsupported handling; nothing was disclosed. |
| `turn_ack_invalid`, `tool_request_forbidden`, `event_forbidden`, `tool_event_forbidden`, `rate_limits_invalid`, `runtime_warning`, `turn_failed`, `model_rerouted`, `completion_mismatch` | The child broke the isolation contract or Codex reported a turn failure. | Terminal for this attempt. A repeated `event_forbidden` or `tool_event_forbidden` on the same runtime means the cell no longer matches Codex behavior: file it, do not widen the allowlist locally. |
| `agent_message_count`, `output_empty`, `output_oversize`, `event_limit` | The turn completed but did not yield exactly one bounded final answer. | Terminal. `event_limit` on a legitimately long answer is a budget question for the cell. |
| `output_not_json` | The final answer was not strict JSON (prose, fenced code, trailing text). | Terminal; not retried. |
| `judgment_envelope_invalid`, `judgment_enum_invalid`, `judgment_refs_duplicate`, `judgment_refs_invalid`, `judgment_conclusion_mismatch`, `judgment_text_bounds`, `judgment_shape_invalid`, `judgment_invariant_invalid` | Strict JSON that failed the frozen judgment contract at the named stage. | Terminal (`response_schema_invalid`); asking again is not a fix. `judgment_refs_invalid` is the model citing an item id instead of a `citable_refs` entry. |
| `request_failed`, `transport_failed`, `deadline_expired`, `cleanup_unconfirmed`, `unclassified` | Runtime transport, deadline, or cleanup ambiguity. | Per ADR-006: pre-acknowledgement transients may retry; post-acknowledgement ambiguity is `outcome_unknown` and is not retried. |

`semantic_case_content_over_item_limit` is a separate coverage gap on the disclosed case; it is
reported alongside a stage, never inferred from one.

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
5. browser and device login timeout, cancellation, malformed completion, process exit, and
   configuration-write failure leave no partial Yoetz binding while process groups and pipes are
   cleaned up within their bounds;
6. disconnect and rollback leave unrelated Codex installations, homes, settings, and sessions
   byte-unchanged.

Do not call login, a model listing, unit tests, or one clean judgment proof of this checklist.

# tests/capability/ — pinned Codex, MCP, platform, and optional-adapter capability evidence

**Wave:** D–F | **ADRs:** ADR-003 through ADR-009 | **Imports (spec-tree):** installed skill,
CLI/MCP, local service/control/vault/privacy, Codex importer, provider/key/platform specs |
**Imported by:** support matrix and release claim gate

## Purpose

Turn external integration claims into dated empirical evidence against exact installed artifacts and
pinned external versions. Documentation and API shape are inputs, not proof. The suite determines
the minimum supported, maximum tested, denied, and untested surfaces advertised by Yoetz v0.1.

## Public surface

```text
tests/capability/
  test_codex_conduit_harness.py
  test_codex_config_and_startup.py
  test_codex_six_tools.py
  test_codex_optional_required_failure.py
  test_codex_skill_discovery.py
  test_codex_parent_subagents.py
  test_codex_resume_reattach.py
  test_codex_jsonl_import.py
  test_codex_timeout_cancellation.py
  test_mcp_gate1_protocol_conformance.py
  test_mcp_protocol_and_sdk.py
  test_local_control_channel.py
  test_platform_filesystem_keyring.py
  test_provider_profile_live.py
  test_privacy_provider_and_local_model_profiles.py
  test_service_keyring_unlock.py
  test_user_presence.py
  test_session_event_monitor.py
  test_observation_dogfood_matrix.py
  evidence.py
```

Live tests are opt-in, budgeted, redacted, and run only in isolated release jobs with explicit
credentials. Codex/MCP local capability tests are mandatory on every advertised platform.

Each case emits a canonical `CapabilityEvidence` record:

- schema/case ID and requirement/claim IDs;
- candidate artifact/resource/fixture digests;
- OS/CPU/ABI/Python/APSW/SQLite identities;
- external tool/protocol/SDK/provider/key-backend exact identities;
- sanitized argv/config profile and integration channel;
- start/end timestamps, bounded duration, outcome `pass|fail|unsupported|inconclusive`;
- transcript/source artifact digest and encrypted/redacted evidence locator;
- observed limitations/reason codes;
- test implementation revision and evidence digest.

No raw prompt/source/tool output/credential/path enters public evidence.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
tests/capability/evidence.py
tests/capability/test_codex_conduit_harness.py
tests/capability/test_codex_config_and_startup.py
tests/capability/test_codex_jsonl_import.py
tests/capability/test_codex_optional_required_failure.py
tests/capability/test_codex_parent_subagents.py
tests/capability/test_codex_resume_reattach.py
tests/capability/test_codex_six_tools.py
tests/capability/test_codex_skill_discovery.py
tests/capability/test_codex_timeout_cancellation.py
tests/capability/test_local_control_channel.py
tests/capability/test_mcp_gate1_protocol_conformance.py
tests/capability/test_mcp_protocol_and_sdk.py
tests/capability/test_observation_dogfood_matrix.py
tests/capability/test_platform_filesystem_keyring.py
tests/capability/test_privacy_provider_and_local_model_profiles.py
tests/capability/test_provider_profile_live.py
tests/capability/test_service_keyring_unlock.py
tests/capability/test_session_event_monitor.py
tests/capability/test_user_presence.py
```

## Behavior

### Codex version matrix

Test each candidate supported Codex version from the built release environment, beginning with
observed local `0.139.0` and target/max-tested `0.144.5` until refreshed at release.
Newest stable is re-probed before ADR acceptance/release. Do not infer intermediate support merely
from endpoint versions; run the critical matrix for each advertised bound and representative range.

### Configuration and startup

- user and trusted-project MCP registration with real supported config field shapes;
- `codex mcp get yoetz --json` same-name preflight; an unrelated existing entry is preserved and
  registration is refused because current `mcp add` replaces the same-name global entry;
- exact command/env/cwd, optional `required=false` and required server policy;
- cold/warm startup percentiles with margin below Codex default timeout;
- schema/resource/startup diagnostic gate finishes before stdin;
- untrusted project behavior and no silent config/skill overwrite;
- malformed command, missing executable, incompatible protocol, stdout noise, slow startup.

### Six operations

Through interactive Codex and `codex exec` where supported:

1. discover exactly six Yoetz tools with frozen schemas/annotations;
2. start, atomic publish, deterministic check, respond, status, receipt;
3. validate structured result and compact summary, IDs/frontiers/coverage/limitations;
4. exercise invalid input, application error, unexpected fenced error, cancellation and response
   loss/idempotent retry;
5. run strict-local with network denied and no provider secret.

Record Codex-visible transcript at the public JSONL/tool boundary and compare with durable ledger;
do not claim access to hidden internal reasoning.

### Optional vs required failure

Optional server failure lets Codex work continue and the skill discloses no live ledger/receipt.
Required server failure is tested separately for `codex exec`, interactive CLI, and every other
advertised surface; only surfaces that empirically block are allowed that claim. Neither path
invents Yoetz state or describes unperformed checks.

### Skill discovery and integration

- explicit `$yoetz` discovery and, if advertised, implicit trigger on material task;
- app-server `skills/list` (or the exact successor discovery API) resolves the managed path and
  reports no load error; duplicate `yoetz` skills across project, ancestor, user, and plugin roots
  make discovery ambiguous and fail the support cell rather than relying on load order;
- trivial-task non-trigger;
- source/wheel/installed skill/reference byte parity;
- preview/consent/diff, modified local copy protection, status/remove;
- ten-step workflow compliance and degraded wording;
- skill/MCP compatible/incompatible version pairs.

### Publication ceremony and large inventories

Run an installed-artifact 100-file generated/migration task with at least three independently
reviewable work packages. The conforming agent creates package/outcome obligations, publishes only
material package transitions, and links bounded manifest evidence for leaf files. Reject a
schema-valid comparison run that creates one obligation or routine publication per file. Record
publications per work package, model-authored event bytes, token/latency overhead, skipped checks,
stale/abandoned ledger state, and user-visible chatter for E-014; file count alone sets no budget.

### Parent, subagents, resume

Run a synthetic two-subagent task:

- parent starts and publishes plan/assignments;
- each subagent attaches/publishes distinct logical writer/actor assertions through same service;
- one contradiction and one evidence-backed result are visible to parent status/check;
- no attribution upgrade or duplicated writer chain;
- parent integrates/responds/rechecks/receipts.

Interrupt/compact/exit and resume by supported Codex mechanism. Reattach same task/session, query
status, preserve request/writer sequences, and publish no duplicate event. If Codex cannot propagate
MCP context to subagents in a version, record the exact limitation and narrow the claim.

For each exact capability profile, verify the reviewed hook-map entry rather than inferring from a
neighboring version. A trigger-present cell fires the frozen compaction event, coalesces duplicate
notifications, runs one bounded re-grounding sequence, avoids recursive triggers, and leaves
coverage unchanged. A trigger-absent cell follows the same manual resume guidance. Trigger failure
does not block optional-host work, publishes no observation, and fabricates no successful attach.

The same workflow captures ADR-011 structural subject state before and after one material edit.
Installed-artifact output is path/content-free, bounded, comparable across resume, and unreachable
from trusted service composition; unsupported/racing/over-cap cases return no state digest.

### JSONL import

Capture public `codex exec --json` fixtures for each supported version: lifecycle, command,
file change, MCP, model message, plan, web search, unknown/new line, malformed/truncated stream.
Importer retains exact source bytes/digest encrypted, maps only justified categories, caps authorship
at harness-observed, quarantines unknown/malformed, and reports gaps. Cooperative-vs-import review
must expose the seeded omitted material event.

### Cancellation/timeout

Cancel before request, during server application, around commit/response, and after Codex client
timeout. Confirm outcome-unknown messaging, same-request retry, no duplicate effect, cancellation
not wrapped as internal error, and clean next tool call/session shutdown.

### MCP SDK/protocol

Run pinned `mcp==1.28.1` low-level server/inspector-style negotiation for protocol
`2025-11-25` and any supported fallback, direct structured results, input validation disabled
at SDK/Yoetz-owned validation active, output schema, `isError`, null-ID parse-error candidate,
EOF/cancellation. Re-run gate before adopting stable MCP v2; prerelease never silently enters release.

### Platform, key, provider optional evidence

On each advertised platform, record path/sync/network detection, owner permissions/fsync/rename/
directory fsync, SQLite source/options, and supported key-backend disposable round trip. Locked/
missing/headless fallbacks remain distinct.

Provider live case names one provider/endpoint profile/model/SDK pair, sends only synthetic minimized
case, checks structured success/refusal/incomplete/rate-limit/timeout where safely reproducible,
provenance/usage, and deterministic post-validation. It has explicit spend/request/time cap and is
not required for strict-local correctness. It is eligible only when a trusted release operator has
already started a ready isolated service and provisioned a disposable provider credential through
the approved confidential ingress/keyring ceremony outside the test process. The capability job
receives only non-secret provider profile identity and bounded service readiness; if that ceremony
is unavailable, the cell is unsupported or inconclusive rather than bypassed.

The local-service capability cells separately prove same-UID peer credential authentication and
owner-only endpoint replacement resistance, approved keyring `locked|ready` behavior, mandatory
session-lock/suspend relock, and fail-closed monitor absence on every advertised platform. The
privacy/provider/local-model cell proves all four privacy profiles, exact endpoint binding, no
repository/storage/environment handles passed through composition, reviewed bundled-adapter
avoidance of forbidden APIs, local-model AF_UNIX-only operation, five-channel independence, and
structural receipts. It does not claim same-UID sandbox isolation. Passing one cell never implies
support for an untested platform/backend/model.

That cell also covers all five review-context profiles and the exact canonical packet seen by the
provider. For every externally advertised endpoint it records the versioned data-use profile and
shows that current eligible evidence controls only the upstream assisted recommendation unless the
editable runtime guard is enabled. Provider statements are recorded as evidence-bound posture,
never as proof of downstream training, retention, or human-access behavior.

The user-presence cell separately proves exact OS-authenticated prompt/action binding and one-use
attestation and emits the exact artifact-bound `user_presence_cells` row. TTY or same-UID identity
is a negative control. A pristine keyring-mode capability claim requires an exact passing keyring
cell and presence cell for the same candidate artifact/release cell; missing presence prevents all
keyring/vault mutation and leaves setup required. Existing keyring vaults without current presence
remain external-egress-disabled/ready-local for durable authority changes. Support is never
inferred from platform name.

## Errors and edge cases

- External rate limit/outage yields `inconclusive`/`fail` evidence, never pass; release
  policy decides whether to retry or narrow support.
- Provider credentials and vault secrets never enter the test process, child environment, argv,
  config, fixtures, or evidence. Tests exercise only an opaque service-owned configured profile.
- Capability transcripts are encrypted private evidence; the public record contains digest and
  bounded observation summary.
- A newer version than maximum-tested is “untested,” not automatically supported; denied versions
  name exact breaking evidence.
- Tests do not mutate the user's real Codex config, skills, HOME, keychain, or repositories.

## Invariants

1. Every external capability claim names tested versions/artifacts/platforms.
2. Evidence is reproducible, bounded, privacy-safe, and tied to exact bytes.
3. Documentation never substitutes for observed behavior.
4. Optional failure degrades honestly; strict-local remains useful.
5. Caller-asserted identity is never upgraded by optimistic interpretation of transcripts.
6. Range changes require fresh evidence and release manifest update.

## Tests

```bash
uv run --locked pytest tests/capability -m "not live_provider and not live_keyring" -q --timeout=600
uv run --locked pytest tests/capability -m live_provider -q --timeout=180
```

The live command runs only in an approved, budgeted release job. Evidence completeness and public
redaction are themselves validated before a support matrix is published.

## Open questions

None.

E-002 is the sole central Codex-version gate.

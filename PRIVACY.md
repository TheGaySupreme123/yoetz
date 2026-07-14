# Yoetz Core privacy

> **Specification-stage notice:** this repository does not contain the Yoetz Core product
> implementation yet. The rules below are design commitments being frozen before implementation,
> not claims about working software. Artifact-bound tests must pass before any release advertises
> them as implemented guarantees.

Yoetz Core is designed around one trusted persistent local service. The service—not the CLI, MCP,
an agent, an LLM, or a future ordinary UI—owns encryption keys, decrypted local state, privacy
policy, provider credentials, and outbound dispatch. Normal clients exchange only bounded control
requests and results.

## Planned default and disclosure path

External LLM disclosure is denied by default. Every proposed external request must traverse one
central path:

`candidate context → classification → effective policy → minimization/redaction/secret scan →`
`optional exact local-human preview → one-use authorization → bound gateway/provider → local receipt`

Provider adapters receive an already approved bounded case through reviewed interfaces. The v0.1
working design permits only bundled adapters and passes them no repository, storage, environment,
or transcript handles. This is not an OS sandbox: a malicious adapter already executing inside the
trusted service process is part of the unresolved trust boundary.

The four LLM privacy profiles are `local_only`, `confirm_every_request`, `minimal_external`, and
`trusted_provider`. Trusted-provider permission always binds named categories, purpose, scope,
provider, model, and endpoint profile; it never means “send everything available.” Telemetry,
crash diagnostics, update checks, and capability testing have separate policies. Enabling one
channel never enables another. A separate global network ceiling defaults off; turning it on
authorizes no channel by itself. `local_only` governs LLM disclosure and may coexist with a
separately consented bounded structural non-LLM channel. True Core zero-network mode is the
composite of `local_only`, the global ceiling off, and all five network channels disabled.
That mode still permits only exact release-profiled local IPC required for the Yoetz service,
confidential helper, approved local model, OS credential/user-presence service, and session-
lifecycle monitor; it does not permit arbitrary AF_UNIX destinations.

The v0.1 working manifest contains no transport implementation for product telemetry, crash
diagnostics upload, update checks, or capability testing. Setup shows those four channels as
unavailable/off; policy cannot create dormant consent, and a future installed capability requires a
fresh local-human transition before it can send anything.

## Non-overridable never-send content

Candidate or user content containing the following must never become model input or another
approved disclosure:

- encryption keys and recovery or unlock secrets;
- passwords and candidate/user-discovered API keys, authentication tokens, cookies, or private
  certificates;
- keyring contents, credential files, hidden authentication configuration, or unrelated
  environment variables;
- opportunistically accessible raw databases, unrestricted logs, stderr, or complete transcripts;
- unrelated files or content outside the authorized workspace/task/request scope.

No privacy profile, plugin, provider label, MCP request, agent message, or human click can waive
this set. Classification and scanning are defense in depth; uncertain content fails closed.

A separately provisioned provider credential presents a necessary founder clarification: the
working design permits a fresh one-attempt vault handle to emit that credential only as
authentication metadata to the exact pinned TLS endpoint. It never enters the model body, context,
preview, receipt, log, environment, configuration, or reusable SDK state. Encryption, unlock, and
recovery secrets have no such exception. Choosing literal zero credential egress would disable
credentialed external providers.

## Local models and human authority

A local model receives plaintext only after the same classification, minimization, scope, and
never-send checks. Core itself uses an exact AF_UNIX-only path and performs no model launch,
download, DNS, or IP networking. A separately running model process is nevertheless a trusted
local disclosure sink unless its exact support cell proves enforceable no-network sandboxing.

Durable policy widening and provider-credential changes require action-bound OS user presence or a
separately designed confidential reauthentication mechanism. Ordinary MCP/agent schemas cannot
grant that authority. Under the current working design, exact foreground approval may authorize one
`confirm_every_request` case already inside durable policy; it cannot widen policy or create a
reusable grant, and it is not cryptographic proof against arbitrary malicious same-UID code.

A usable OS keyring does not itself prove action-bound human presence. Under the current safe
default, pristine automatic keyring initialization requires both verified keyring create/load and
an exact artifact-bound user-presence adapter. Without both, the service writes no vault/keyring
artifact, remains `uninitialized/locked`, and lets a local human explicitly choose passphrase
setup—never as a silent fallback. That explicit choice requires proven-pristine local state,
allocates a fresh installation identity, and may proceed when keyring is locked/unavailable without
claiming it proved entry absence; committed passphrase mode never later probes or uses keyring. An existing keyring vault may remain ready for local work when
user-presence authority is measured unavailable at ready/recomposition, or when a human-control
operation explicitly observes its failure; external activation and durable authority changes then
stay fenced until fresh validation. v0.1 does not claim an asynchronous presence watcher. A separate
admin-authorization secret or additional platform presence implementation remains a founder decision.

## Local evidence and configuration

Each successfully reserved terminal outbound decision and every physical attempt produces a local
structural receipt with destination, policy/scope/category decisions, sizes, transformations,
consent source, bounded outcome, and—only for a physical attempt—a keyed commitment to the final
provider application-body bytes. Waiting for a human, approval, and consumed-but-unreceipted work
remain resumable internal states, not finished receipts. If the initial audit reservation itself
fails, Yoetz fails closed before preview/authorization/dispatch and returns bounded `audit_failed`
without fabricating a receipt; this is the sole no-receipt decision exception. Receipts contain no
request body, credential, header, path, raw response, or unbounded provider error. Content-bearing
v0.1 privacy proposals are encrypted in their owning task bundle; taskless unavailable-channel
decisions contain structural data only. A future taskless content-bearing channel requires a new
reviewed encrypted-audit storage contract. The installation privacy catalog—not a fabricated task-
ledger row—keeps each proposal object live for the supported installation-data lifetime. v0.1 has no
individual privacy-audit-content deletion operation: ordinary task redaction and orphan cleanup do
not remove these objects. Verified backups include their encrypted bytes plus a structural audit
sidecar; restore never revives pending approval or dispatch authority. LLM inference consent is
independent of telemetry, crash diagnostics, update checks, and capability testing.

The enforceable technical contract, setup behavior, schemas, fixtures, and unresolved founder
choices are specified in:

- [`docs/adr/ADR-009-data-egress-privacy.md`](docs/adr/ADR-009-data-egress-privacy.md)
- [`specs/docs/protocol/data-egress-and-privacy.md.md`](specs/docs/protocol/data-egress-and-privacy.md.md)
- [`specs/docs/protocol/privacy-setup-wizard.md.md`](specs/docs/protocol/privacy-setup-wizard.md.md)
- [`specs/schemas/privacy/privacy-policy-1.0.0.schema.json.md`](specs/schemas/privacy/privacy-policy-1.0.0.schema.json.md)
- [`specs/OPEN_QUESTIONS.md`](specs/OPEN_QUESTIONS.md)

Until those decisions are ratified and their release evidence passes, public claims remain
explicitly “not yet evidenced.”

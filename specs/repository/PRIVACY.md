# PRIVACY.md — public privacy promise and user controls

**Wave:** C/E/F | **ADRs:** ADR-004, ADR-006, ADR-008, ADR-009, ADR-011 | **Imports (spec-tree):**
`docs/protocol/data-egress-and-privacy.md`, `docs/protocol/privacy-setup-wizard.md`,
`schemas/privacy/privacy-policy-1.0.0.schema.json` | **Imported by:** repository readers,
setup surfaces, public claim map, privacy conformance and packaging tests

## Purpose

Give users one plain-language, public account of what stays local, what may leave the machine,
who can authorize disclosure, and what evidence Yoetz records. The document is a user promise,
not a substitute for the enforceable protocol and tests.

## Public surface

The future root document has these stable sections:

1. Local service trust boundary
2. Default privacy posture
3. Content classes and never-send data
4. Four LLM privacy profiles
5. Review-context profiles and the assisted-review recommendation
6. Independent network channels
7. Setup and later policy changes
8. Human involvement versus direct-to-agent review
9. Per-request preview and approval
10. Local structural subject-state capture
11. Local egress receipts
12. Encryption, locking, and confidential unlock
13. Limitations and threat model
14. How to inspect or report a privacy problem

Examples use synthetic content and show both an allowed and blocked disclosure. The document links
to the exact technical protocol, policy schema, security policy, and evidence-bound public claims.

## Behavior

The document states that Yoetz runs as a trusted local service. CLI, MCP, and future UI are
control surfaces; they never own encryption keys or receive decrypted vault-unlock secrets. A
keyring-backed vault, or a pristine installation eligible for automatic keyring initialization,
first tries the approved OS keyring and may otherwise remain locked. A committed passphrase-backed
vault never probes or silently falls back to the keyring; it starts locked until a local human
unlocks it through the separately specified confidential local channel. Keys and unlock secrets
never appear in normal CLI arguments, MCP messages, agent/LLM or ordinary application prompts,
environment, configuration, logs, traces, transcripts, or shell history. The one permitted
passphrase UI is the separately typed local no-echo unlock prompt, which connects directly to
confidential service ingress and refuses stdin/pipes/fallback input.
Pristine automatic keyring initialization is available only in an exact release-tested platform
cell that proves both keyring create/load and `UserPresencePort`. An existing keyring-backed vault
may become ready for local work when presence is measured unavailable at ready/recomposition, or
when a human-control operation explicitly observes its failure, but external provider activation
remains fenced until presence is restored and revalidated. v0.1 claims no asynchronous presence watcher.

The safe installation default is `local_only`, `review_context_profile=structural`, with
`network_egress_permitted=false`, all five
network channels denied, and no local model. The four privacy profiles govern LLM inference/content
disclosure only. Under `local_only`, external LLM-provider adapters cannot be constructed and Yoetz
makes no external user/task-data request. Future policy may separately authorize the global ceiling
and one bounded non-LLM row without enabling external LLM inference. v0.1 ships no production
transport for telemetry, crash upload, update checks, or capability testing: setup marks those rows
unsupported/off and rejects proposed enablement as `channel_unavailable` without storing consent or
making a network attempt. A forced/imported enabled state writes only a no-dispatch structural
decision receipt. A later implementation requires an exact public owner and fresh local-human
confirmation; it cannot silently activate an old draft/answer after upgrade. These channels remain independent
and can never carry task/user content. A separately selected local model is a protected disclosure
sink subject to scope, minimization, scanning, and never-send rules, but it receives plaintext. A
pre-existing model runtime is an explicitly trusted local component unless its exact support cell
proves enforceable no-network isolation; Yoetz's AF_UNIX-only delivery does not establish that a
separate process lacks ambient networking (F-013).
The other profiles are described exactly:

- `confirm_every_request`: a local human previews categories and bounded excerpts and approves each
  physical external request; every retry gets a fresh exact preview and decision, while crash-resume
  before any attempt continues the same one-dispatch approval;
- `minimal_external`: Yoetz automatically sends only the smallest policy-approved, minimized,
  redacted, secret-scanned case;
- `trusted_provider`: a local human authorizes named content categories for one provider, endpoint
  profile, model policy, scope, and purpose; this never means unrestricted access.

The document distinguishes that zero-egress seed from the CLI's recommended *configured* semantic
review recipe. `ReviewContextProfile` is independently `structural|goal_aware|assisted|expanded|
custom`; it only selects candidate material and cannot widen the privacy policy. `assisted` includes
goal/obligation/claim/decision/finding prose, a material timeline, deterministic finding bases,
change/coverage facts, and bounded problem-local evidence/test/failure/diff/repository excerpts
already recorded at the frozen frontier. It excludes sensitive/confidential and transcript content
by default, and says plainly that v0.1 has no live Git/filesystem source broker for semantic cases.

ADR-011's separate `yoetz state capture` support command is the sole narrow v0.1 exception to the
ordinary client's no-repository-access rule. The local CLI accepts one explicit trusted worktree,
uses the bounded structural Git adapter, performs no network I/O, and returns only a closed status,
format/version metadata, counts, limitations, and state digests. It returns no source bytes, diff,
path, filename, commit message, author, remote, branch, or credential-bearing Git configuration;
the service and MCP surface receive no ambient repository handle. Capture does not populate a
semantic-review case and cannot upgrade authorship, observation, or verification coverage. An
unbounded, unsupported, racing, or unsafe worktree fails closed without a subject-state digest.

Two local disclosures are deliberately separated from all of this. Ordinary human-readable output on
your own terminal is the `local_human_view` sink: reading a finding you asked for, on a vault you
unlocked, on your own machine, is not a disclosure to anyone, and no privacy answer gates it.
Releasing content to an *agent-capable host* is the `agent_context` sink, and that one is gated —
because that host may forward its context to its own provider, which Yoetz cannot see or promise
anything about. That sink is conditioned on authorship: an agent always receives back its own
published material and the deterministic findings computed solely from it, because that content is
already in its context and withholding it would protect nothing. Material the agent did not
author — another writer's work, imported records, and a semantic reviewer's prose — stays gated
until you widen it. Never-send and sensitive/confidential content are absolute at every sink and
under every authorship.

The recommended recipe maps to a standing workspace-scoped `trusted_provider` policy with
per-request preview off, public-structural plus ordinary-user-content classes, exact listed
categories, and agent-context permission for `finding_summary` so the reviewer's challenge — which
the agent did not author — can reach it. It is offered only for a current
endpoint data-use record stating training `prohibited`, retention `none|bounded`, and provider human
access `prohibited|restricted`. Known-broad, unknown, or stale posture removes the recommendation.
The recipe enables the editable current-evidence guard; a trusted custom loosening can disable it
but cannot retain the upstream no-training claim. The text calls this recommendation evidence, not technical
proof. Users may inspect/edit every answer, choose stricter/broader/custom behavior, or fork the
open-source project; modified forks do not inherit upstream evidence claims.

All outbound LLM requests traverse `candidate context -> classification -> effective user policy ->
local minimization/redaction/secret scan -> optional human preview of the exact prepared case ->
outbound gateway -> bound adapter`. Provider adapters receive only a validated outbound case. They
are passed no repository, database, transcript, environment, log, file, or other ambient handle.
v0.1 loads only reviewed bundled adapters from a closed registry; third-party/dynamic adapters are
absent. This is not an OS sandbox—a malicious adapter already executing inside the trusted Python
service could use the active account's ambient authority. The service revalidates the case after approval; it never asks a human to approve raw
candidate content and then silently changes what will be transmitted.

The document enumerates the non-overridable never-send set: encryption/recovery secrets;
passwords plus candidate/user-discovered API keys, auth tokens, cookies and private certificates;
keyring content; unrelated
environment variables; credential/hidden authentication files; opportunistically accessible raw
databases; unrestricted logs, stderr or transcripts; and unrelated out-of-scope files. A permissive
profile cannot waive this set.

Resolved decision F-012 distinguishes candidate/user-discovered credentials, which are always
blocked from model content, from one separately provisioned service-vault credential used only as
one-attempt authentication metadata to the exact profile-bound HTTPS endpoint selected by the
reviewed provider registry, with platform CA trust and hostname validation. v0.1 does not claim
certificate or SPKI pinning.
That provider credential never enters body/context/preview/receipt/log/SDK state; it does necessarily
leave the machine in the authentication header. The alternative is to forbid credentialed external
providers.

`network_egress_permitted` is a global ceiling. When false, all five channels are off. When true, it
does not enable any channel. LLM inference, telemetry, crash diagnostics, update checks, and
capability testing then have independent policies and consent; enabling one never enables another.
In v0.1, a proposed enabled non-LLM row is rejected and performs no I/O; no dormant permission is
stored.
True zero-network operation is the composite `local_only` + false global ceiling + all five channels
denied. It still permits only exact local IPC needed to operate: service/confidential control,
separately approved local-model disclosure, and release-tested OS credential/user-presence/session-
lifecycle security IPC (for example allowlisted Linux AF_UNIX session-bus Secret Service routes
and, separately, system-bus `org.freedesktop.login1` routes, or macOS native security/presence/
session notifications). It never permits
arbitrary AF_UNIX, arbitrary bus methods/peers, or a local
proxy. The evidence names the exact platform profile, Yoetz-owned service/client/helper processes,
startup-through-`locked|ready` lifecycle interval, operations, and allowlisted peers. OS agents and
separate local-model runtimes are outside the Yoetz process claim, with the local-runtime limitation
stated below. Policy tightening is immediate;
widening the global ceiling, content, provider, purpose, endpoint, scope, or a network channel
requires explicit local-human confirmation through a trusted control surface. MCP, agents,
providers, plugins, and LLM output may request more context but cannot authorize or apply the
widening.

Once the standing assisted policy is committed, ordinary checks, automatic retries, reviewer
findings, agent responses, and rechecks run without human prompting. The reviewer addresses the
main agent through an ordinary semantic finding and can request action, evidence, claim revision,
an evidence-backed dispute, or an explicit unresolved limitation. Human involvement remains for
policy widening, credential mutation, `confirm_every_request`, and finding waiver; never-send and
out-of-scope content are unapprovable.

v0.1 crash diagnostics are bounded structural identities/counts only. Yoetz does not capture or
upload exception messages, locals, source/path excerpts, or raw tracebacks, even to an owner-only
file. A future encrypted diagnostic-content feature would require its own reviewed schema,
encrypted storage, retention, never-send/minimization policy, explicit local privacy authorization,
and release evidence; enabling crash diagnostics today does not enable such capture.

Every successfully reserved terminal external-request decision and every physical attempt leaves a
local structural egress receipt recording decision, provider/model/endpoint profile, policy version,
authorization scope, approved and blocked category names, byte/token counts,
redaction/minimization counts, and consent source. A physical-attempt receipt additionally contains
a keyed commitment to the exact final provider/application request-body bytes; a pre-dispatch
receipt forbids that commitment and attempt-body count. Waiting, approval, and
consumed-but-unreceipted work are resumable audit states rather than finished receipts. Initial audit
reservation failure is the sole no-receipt exception: it returns bounded `audit_failed` before
preview/authorization/dispatch and fabricates no receipt identity. Credential-bearing auth metadata,
transport-generated fields, and HTTP/TLS framing are outside that commitment. It contains no
plaintext request, secret, credential, path, prompt, response, or unbounded provider error. Denials
are recorded without retaining the denied content. Each physical attempt uses a fresh
endpoint/profile/body-digest/deadline-bound credential callback; the provider SDK never retains the
real key.

Content-bearing v0.1 privacy proposals are encrypted as task-bundle privacy-audit objects referenced
from the local catalog. Taskless unavailable-channel decisions and machine policy diffs contain only
closed nonsecret structural fields. A future taskless content-bearing channel cannot activate until
a separately reviewed installation-scoped encrypted audit-object owner, key, retention, backup, and
recovery contract exists. Catalog references remain explicit live roots for the supported
installation-data lifetime and do not require task-ledger inventory. v0.1 has no individual
privacy-audit-content deletion operation; ordinary task redaction/GC cannot remove them. Backup
includes the encrypted objects and canonical structural privacy sidecar. Same-installation route
move preserves every current root; clean restore preserves terminal evidence but expires pending/
approved/authorized state and resolves ambiguous consumed work without reviving dispatch authority.

The threat section is precise: encryption protects local payloads under the documented key/service
assumptions; it does not protect data after an authorized provider or trusted local-model runtime
receives it, a compromised trusted service process or in-process bundled adapter, or a
user-authorized disclosure. Public copy never promises anonymous,
zero-knowledge, forensic erasure, or universal secret detection.

## Errors and edge cases

- A locked service is not described as corrupt, deleted, or silently reset.
- `local_only` means Yoetz performs no external LLM or user/task-content egress, not “a provider call
  with redacted content.” It does not by itself mean that separately authorized bounded structural
  telemetry, diagnostics, update checks, or capability tests are disabled. If a local model is
  enabled, the UI names the separate runtime trust/sandbox limitation.
- Local-model permission gives the Yoetz adapter no IP-network, launch, or download capability; it
  does not by itself remove a pre-existing runtime process's ambient authority.
- Local OS keyring/user-presence IPC is not called network egress only for the exact release-tested
  platform route and peer/method allowlist; arbitrary local sockets, D-Bus calls, and proxies remain
  forbidden.
- The document never suggests that credentials can be supplied through MCP, normal CLI flags, env,
  config, logs, or prompts.
- A receipt proves the gateway decision and committed final request-body bytes, not provider deletion,
  confidentiality, truthfulness, successful inference, authentication-header identity, or complete
  HTTP/TLS wire framing.
- Any public claim remains qualified as not yet evidenced until artifact-bound tests pass.
- A v0.1 `channel_unavailable` receipt has no dispatch ID/time, request commitment, authorization
  consumption, DNS, or socket attempt; it is not evidence that a transport ran or failed
  ambiguously.

## Invariants

1. The trusted local service is the sole owner of keys, unlocked local state, and egress enforcement.
2. Never-send categories cannot be overridden by any profile or control surface.
3. The global network ceiling grants nothing; separate network channels require separate consent.
4. Only a local human on a trusted surface can loosen policy.
5. Provider adapter composition supplies only an approved bounded outbound case and no ambient
   handles; v0.1 does not claim process sandboxing against malicious in-process code.
6. Privacy receipts are structural and commitment-bearing, never plaintext request logs.
7. v0.1 has no raw traceback capture, and provider credentials are one-attempt transport-only
   capabilities rather than SDK client state.
8. Public zero-network claims require `local_only`, the false global ceiling, and all five channels
   disabled; they are never inferred from the profile token alone.
9. v0.1 has no production non-LLM network transports, and a later capability cannot activate a
   stored choice without fresh local-human authorization.
10. `confirm_every_request` never authorizes a hidden retry budget: one foreground decision permits
    one physical dispatch.
11. Zero-network evidence names its exact Yoetz process/readiness boundary and allowlisted local OS
    IPC; it does not attest external OS agents or model runtimes.
12. Privacy receipts are terminal; pending/approved/receipt-repair state is never presented as a
    finished outcome, and initial reservation failure is explicitly unreceipted and pre-dispatch.
13. Review-context selection never grants disclosure, and absent/hidden code is never rendered as
    observed unchanged code.
14. Structural subject-state capture is local, explicit, bounded, content-free, and non-networked;
    it grants no repository authority to the service, MCP surface, or provider path.

## Tests

- `tests/conformance/privacy/test_privacy_profiles.py`
- `tests/conformance/privacy/test_never_send_scope_and_channels.py`
- `tests/conformance/claims/test_public_claim_map.py`
- `tests/packaging/test_privacy_docs_and_resources.py`
- `tests/packaging/test_private_boundary_and_secret_scan.py`
- `tests/subprocess/test_cli_invocations.py` proves the bounded local capture and its output/privacy
  boundary; `tests/packaging/test_service_boundary_imports.py` proves the adapter is unreachable
  from trusted service composition.

## Open questions

None.

# docs/public-claims.json — evidence-bound public product claim map

**Wave:** F | **ADRs:** ADR-004, ADR-005, ADR-007, ADR-008, ADR-009 |
**Imports (spec-tree):** public README/privacy/protocol specs, capability/conformance/packaging
tests, release evidence | **Imported by:** README claim lint, release evidence generation,
public-claim conformance test

## Purpose

Prevent product copy from outrunning executable evidence. Every material public statement about
privacy, durability, compatibility, verification, integration, portability, or support maps to its
scope, limitations, owning tests, and artifact-bound release evidence.

## Public surface

A canonical strict-JSON document with `schema: "yoetz.public-claims/1"`, `version`, and an
ASCII-sorted `claims` array. Each claim contains:

- `claim_id`: stable lowercase dotted token such as `privacy.strict_local_zero_egress`;
- `statement`: the exact bounded sentence or sentence pattern permitted on public surfaces;
- `scope`: exact operation/profile/platform/version constraints;
- `limitations`: sorted nonempty limitation tokens where applicable;
- `surfaces`: sorted repository-relative public files that use the claim;
- `requirements`: sorted ADR/spec requirement IDs;
- `tests`: sorted future test paths that directly assert the claim;
- `evidence_kinds`: required release-evidence record kinds;
- `release_status`: `not_yet_evidenced|evidenced|withdrawn`.

No free-form evidence URL, local path, customer statement, benchmark anecdote, or private incident
identifier is permitted.

## Behavior

The initial map includes at least: six-operation CLI/MCP parity; composite zero network egress
(`local_only`, `network_egress_permitted=false`, all five channels disabled, with only the
authenticated exact local-service/confidential control endpoints, a separately approved exact
AF_UNIX local-model profile, and release-cell allowlisted OS credential/user-presence/session-
lifecycle security IPC allowed);
local encrypted payload storage with explicit threat limitations; generation-fenced single writer;
idempotent retry after ambiguous response loss; deterministic replay; coverage-bounded findings and
receipts; exact-version Codex integration/import; machine-bound versus proven portable recovery;
advertised platform/runtime cells; source/package resource parity; and no forensic-erasure claim.

Privacy claims are separate, directly tested entries:

- `privacy.trusted_local_service_boundary`: the service alone owns keys, decrypted vault state,
  policy authority and the egress gateway; CLI/MCP/UI are bounded control surfaces;
- `privacy.keyring_initialization_presence_gate`: pristine automatic OS-keyring initialization is
  advertised only for an exact release cell that proves both keyring create/load and
  `UserPresencePort`; an existing keyring-backed vault may become ready for local work when presence
  is measured unavailable at ready/recomposition or explicitly observed failing during human
  control, but external activation remains fenced until fresh validation; v0.1 claims no
  asynchronous presence watcher;
- `privacy.local_only_no_external_llm_content`: `local_only` prevents external LLM-provider
  construction and external user/task-content disclosure. It does not claim that a separately
  authorized bounded structural telemetry, diagnostics, update, or capability channel is off;
- `privacy.zero_network_configuration`: Core and its clients perform no AF_INET/AF_INET6, DNS,
  redirects, external provider, telemetry, crash upload, update check, capability-test or
  model-download traffic when `profile=local_only`, `network_egress_permitted=false`, and all five
  channels are disabled. The claim names the exact platform/release profile, Core-owned
  service/client/confidential-helper processes, startup-through-`locked|ready`/operation interval,
  and allowlisted local IPC: service/confidential endpoints, optional exact local-model AF_UNIX, and
  measured OS credential/user-presence/session-lifecycle IPC such as allowlisted Linux AF_UNIX
  session-bus Secret Service routes and, separately, system-bus `org.freedesktop.login1` routes, or
  macOS native security/presence/session notifications.
  No arbitrary AF_UNIX/bus method/peer
  or local proxy is implied; external OS agents and a separate local-model runtime remain outside
  the process claim, and the latter retains its F-013 limitation;
- `privacy.never_send_non_overridable`: every frozen forbidden-data kind in candidate/user content
  is blocked from all network channels and the local-model/agent-context/trusted-human-control
  disclosure sinks; separately provisioned provider-auth metadata is governed by founder gate F-012;
- `privacy.policy_approved_outbound_only`: provider adapters accept only an exact bounded approved
  outbound case and composition grants no repository/database/environment/transcript handles; the
  claim is limited to reviewed bundled adapters and is not OS/process sandbox isolation;
- `privacy.local_human_widening_only`: ordinary agent/MCP/provider/import schemas cannot loosen
  policy; tightening can apply immediately and widening requires strong trusted local-human
  authority. Exact foreground consent for one already-authorized `confirm_every_request` preview
  is separately limited by F-011 and is not policy authority;
- `privacy.confirm_every_attempt`: under `confirm_every_request`, one foreground preview/decision
  authorizes one physical dispatch. Every consumed-attempt retry requires a fresh exact decision;
  only crash/resume before authorization consumption continues the same authority;
- `privacy.independent_network_channels`: the global network ceiling authorizes nothing by itself;
  LLM inference, telemetry, crash diagnostics, update checks and capability testing require
  independent policy and evidence, and `local_only` governs only the LLM branch;
- `privacy.v01_non_llm_channels_unavailable`: v0.1 owns no production telemetry, crash-upload,
  update-check, or capability-test transport. Setup rejects proposed enablement and stores no
  dormant consent. A forced/imported enabled row yields a pre-dispatch `channel_unavailable`
  structural decision with no authorization consumption, dispatch fields, request commitment, DNS,
  or socket I/O; later transport support requires a fresh local-human confirmation and cannot
  silently activate an old draft or answer;
- `privacy.structural_egress_receipts`: every successfully reserved terminal pre-dispatch outbound
  decision and every physical network attempt, including taskless channels, produces a durable
  plaintext-free structural receipt; pending-human, approved, and receipt-repair states are not
  finished receipts. Initial audit-reservation failure is the sole no-receipt exception and occurs
  before preview, authorization, or dispatch. A keyed exact-final-request-body commitment is present
  only for a physical attempt and explicitly excludes credential metadata and HTTP/TLS framing;
- `privacy.audit_content_storage_boundary`: content-bearing v0.1 disclosure proposals are encrypted
  in the owning task bundle and referenced from the privacy catalog; taskless unavailable-channel
  decisions and machine policy diffs are structural only, and no taskless content-bearing channel
  may activate without a reviewed installation-scoped encrypted-audit storage contract. Catalog
  refs are live without ledger inventory, v0.1 offers no individual audit-content deletion, backup/
  route restore preserves the root union, and clean restore never revives disclosure authority;
- `privacy.local_model_same_fences`: Core grants a separately configured local model only an exact
  AF_UNIX disclosure path through the same classification/minimization/never-send fence and never
  launches or downloads it; the separate runtime's own network authority is governed by F-013.
- `privacy.no_raw_traceback_capture`: v0.1 diagnostics retain only bounded structural identity and
  never capture exception messages/locals/source/path excerpts or raw tracebacks in plaintext or
  encrypted form; a future encrypted artifact requires a separate reviewed privacy-authorized
  feature.
- `privacy.one_attempt_provider_credentials`: each physical provider attempt uses a fresh exact
  endpoint/profile/request-body-digest/deadline-bound credential callback in the custom transport;
  no SDK client/default-header object retains the real credential, while the working F-012
  interpretation permits its authentication header only to the exact pinned TLS endpoint.

Each entry names `PRIVACY.md`, the technical protocol, applicable profile/composite policy/fixture, exact privacy
conformance/integration/capability/packaging tests, limitations, and `not_yet_evidenced` until an
artifact-bound release gate passes. Enabling semantic inference never substantiates any other
network-channel claim. Public claim lint rejects “local only means zero network” unless the same
claim also binds the false global ceiling and all five disabled channel decisions.

A release candidate changes `release_status` to `evidenced` only when all named tests passed against
that exact artifact/support cell and their evidence records are included by digest. README/docs/help
lint extracts material claim tokens and requires an active map entry. If evidence expires, fails, or
narrows, copy and map narrow together; the system never keeps a stronger sentence with a warning
hidden elsewhere.

## Errors and edge cases

Duplicate IDs/statements, unknown status, unsorted sets, missing limitations, absent test owner,
unrecognized surface, noncanonical JSON, or an `evidenced` claim without current artifact-bound
evidence blocks release. Marketing adjectives without a testable scope are rejected, not placed in
an unverified bucket.

## Invariants

1. Every material public claim has one stable map entry and direct executable support.
2. Claim wording is never stronger than its weakest named evidence and limitation.
3. Exact-version/platform claims do not imply continuous ranges.
4. The map contains only public structural data and no private provenance.
5. A missing or inconclusive gate leaves the claim `not_yet_evidenced` or removes it from copy.

## Tests

`tests/conformance/claims/test_public_claim_map.py`,
`tests/packaging/test_checksums_sbom_and_provenance.py`,
`tests/packaging/test_private_boundary_and_secret_scan.py`, and release-workflow evidence checks.
Privacy entries additionally require `tests/conformance/privacy/test_privacy_profiles.py`,
`tests/conformance/privacy/test_never_send_scope_and_channels.py`,
`tests/integration/privacy/test_egress_gateway.py`,
`tests/capability/test_privacy_provider_and_local_model_profiles.py`, and
`tests/packaging/test_privacy_docs_and_resources.py`. The no-traceback/credential-lifetime claims
also require `tests/unit/observability/test_logging_allowlist.py` and
`tests/subprocess/test_service_secret_boundary.py`.

## Open questions

None.

Canonical JSON is the frozen committed format; generated human tables are derived views.

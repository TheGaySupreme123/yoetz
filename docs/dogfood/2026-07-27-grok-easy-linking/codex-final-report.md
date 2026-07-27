# Grok / xAI easy-linking dogfood report

Date: 2026-07-27  
Branch: `codex/grok-easy-linking-dogfood-20260727`  
Baseline and current commit head: `3da640a9d4999d38149b2e996dc84ae87edc0295`  
Working tree: intentionally uncommitted; no push, merge, issue, or PR was created.

## Outcome

Grok / xAI easy linking is implemented through the existing reviewed OpenAI-compatible Chat
Completions path. The new exact profile is `xai-openai-chat-completions`, pinned to
`https://api.x.ai/v1` and `/v1/chat/completions`. The operator surfaces are:

```text
yoetz --set --grok --model <model-id>
yoetz provider endpoint --provider grok --model <model-id>
```

`xai` and `x-ai` resolve to the same canonical `grok` preset. The preset uses provider identity
`xai`, default model `grok-4.5`, provider-enforced structured output, and an `unknown` data-use
profile. Unknown data-use posture means the binding is not recommendation-eligible.

This is structural/runtime proof, not live Grok proof. No xAI credential was read, entered,
stored, rotated, or exposed; no active provider binding or privacy policy was changed; and no
xAI request was sent. The live semantic request exercised during the run used the pre-existing
Fireworks personal binding and is recorded below as separate evidence.

## Authority and gap diagnosis

The launch scan found no `grok` or `x-ai` strings in the tracked product tree. The branch pointed
at `main`/`origin/main` at the baseline above. Authority review covered `CONTRIBUTING.md`,
`docs/architecture.md`, `docs/INTERFACES.md`, ADRs 006, 009, 012, and 014, `docs/OPEN_QUESTIONS.md`,
the provider schemas/fixtures, the provider configuration and factory modules, and focused tests.

The real gap was not provider dispatch: OpenRouter and the other existing presets already had a
closed preset, exact endpoint facts, Chat Completions factory, privacy gateway, credential binding,
request digest, semantic normalization, provenance, and receipt path. The missing safe surface was
the equivalent Grok/xAI exact preset and operator linking. The change therefore adds no new
credential, privacy, egress, transport, request, provenance, or receipt mechanism.

Official xAI documentation was checked for the endpoint and structured-output facts:
[Inference REST API](https://docs.x.ai/developers/rest-api-reference/inference),
[Chat Completions](https://docs.x.ai/developers/rest-api-reference/inference/chat), and
[Structured Outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs).
Those sources support the fixed `/v1/chat/completions` surface and `response_format.type` of
`json_schema`; they do not constitute this repository's live capability evidence.

## Files changed

Product and authority files:

- `src/yoetz/config/write.py` — canonical Grok preset, xAI aliases, and builder helpers.
- `src/yoetz/adapters/providers/factory.py` — fixed `api.x.ai/v1` Chat Completions facts.
- `src/yoetz/cli/provider_binding.py` — preset selection and interactive menu entry.
- `src/yoetz/cli/setup.py` — `--grok` setup routing through the existing confidential ceremony.
- `src/yoetz/cli/app.py` — root `--grok`, endpoint `--grok`, and `grok`/`xai`/`x-ai` aliases.
- `docs/adr/ADR-006-semantic-provider-profile.md` — exact profile authority and unknown-data-use
  boundary.
- `docs/adr/ADR-012-first-run-setup-wizard.md` — provider-only shortcut parity.
- `docs/INTERFACES.md` — shared endpoint-profile inventory and factory invariant.
- `docs/OPEN_QUESTIONS.md` — E-007 status remains open; live interoperability is not claimed.
- `docs/usage/providers.md` — operator commands, aliases, and honest verification wording.

Focused tests:

- `tests/unit/config/test_owner_declared_endpoint.py`
- `tests/unit/adapters/providers/test_factory_dispatch.py`
- `tests/unit/adapters/providers/test_chat_completions_request_shape.py`
- `tests/subprocess/test_setup_wizard_cli.py`

This report is the required new file. The other files already present under this dogfood directory
were preserved and not copied into public architecture files.

## Verification

Passed:

- `uv run pytest tests/unit/config/test_owner_declared_endpoint.py tests/unit/adapters/providers/test_factory_dispatch.py tests/unit/adapters/providers/test_chat_completions_request_shape.py tests/subprocess/test_setup_wizard_cli.py` — **72 passed**.
- `uv run ruff check` on all changed Python files — passed.
- `uv run ruff format --check` on all changed Python files — passed.
- `npx --no-install pyright` — **0 errors, 0 warnings, 0 informations**.
- `uv run python scripts/scan_public_boundary.py --source-tree .` — **PASS, 711 files scanned**.
- `uv run yoetz --help` and `uv run yoetz provider endpoint --help` — both expose the Grok
  shortcut without changing configuration.
- Temporary-directory binding exercise — wrote nonsecret Grok config, loaded it back, resolved
  exactly one `ChatCompletionsExternalFactory`, confirmed `api.x.ai` and
  `/v1/chat/completions`, confirmed unknown data-use posture, and confirmed no `api_key` in the
  file. The temporary directory was cleaned by its context manager.
- `git diff --check` — passed.

No generated schema, lock file, release manifest, or frozen artifact was hand-edited.

## Proof boundary and unresolved risks

Structural proof establishes that the preset is closed, the endpoint is pinned, the existing
factory is selected, the request shape is canonical/digest-bound, and credentials still travel
only through the service-owned confidential path. Focused request-shape tests also establish the
xAI profile renders the same strict judgment schema and exact Chat Completions path.

Live proof is absent for Grok/xAI. In particular, this run did not establish a live xAI credential,
service readiness for xAI, xAI privacy authorization, a physical xAI dispatch, xAI provider request
ID/provenance, or replay of an xAI receipt. The default `grok-4.5` model remains a convenience
default, not a current-model guarantee. The provider data-use record remains unknown, so no
assisted/recommendation claim is made. Any future live probe must be independently authorized,
must not reuse the Fireworks binding, and must record exact model/endpoint capability evidence
before the profile can be advertised as live verified.

## Yoetz task, evidence, and receipt

- Task: `tsk_861ccfd3-2781-4d92-91c9-96e4215b28cb`
- Session: `ses_52384bf1-22c4-48eb-8a8a-9ac71379e874`
- Writer: `wri_88b7f46e-ea70-4d91-a127-758965d0cb93`
- Plan frontier: sequence `2`, head
  `sha256:3305ff48c62b1d446362448937f9f64f3c629a9732a57391adae566834f4348e`
- Evidence frontier after accepted durable publication: sequence `6`, head
  `sha256:4335751647d3ddeebc197dd8e71a2c11230a2bda03d57e8f940b4b04518f4fe9`
- Evidence: `evd_7d4e5f6a-8b9c-4d01-8e34-5f6a7b8c9d01`
- Completion claim: `clm_8e5f6a7b-9c0d-4e12-8f45-6a7b8c9d0e12`
- Semantic-required check: request `req_0e1f2a3b-4c5d-4e67-890f-1a2b3c4d5e67`, result frontier
  sequence `7`, head `sha256:5f58a510ac4d23abaa698de4348cc21b2a90673e69943544c41fd931b5010ec4`.
  Yoetz returned `no_issue_detected`, zero findings, and `succeeded/semantic_completed`.
- Semantic provenance from that check is explicitly **Fireworks**, not Grok: provider
  `fireworks`, profile `fireworks-responses`, model
  `accounts/fireworks/models/minimax-m3`, attempt
  `att_dc86cc08-fce1-4e5a-b392-156db497c565`, privacy receipt
  `egr_65894950-a7f7-4db5-bf34-db4d8ad64c04`, provider request ID
  `resp_97b64b05b3164f14a06b2afe8a19d33f`.
- Final Yoetz receipt: `rcp_4b302cb2-a9f1-41a2-99fb-aa74b60372c6`, object
  `obj_22c219d2-cd19-475b-ac5a-5169a2a3ccfe`, digest
  `sha256:66c5736a7678d1bb9a778540bbd803d4664b82a54fe6b188f7403e45bbce1a62`.
  Its conclusion was `no_unresolved_deterministic_findings`, with coverage explicitly bounded to
  published evidence and not proof of correctness.
- Receipt replay with the same request identity returned the same receipt ID and digest.
- Final status read at sequence `8`, head
  `sha256:c3f78bf23cfb1f5454dada50dca4fe18edfb7a13b854fb676b130169489d0a5e`, reported current
  freshness, zero open obligations, zero unresolved findings, and zero reported gaps. Its
  top-level coverage projection was engine-derived/structural; the task page retained the earlier
  cooperative publication plus deterministic/semantic check coverage, so this report does not
  overstate the narrower top-level view.

## Chronological Yoetz guidance and disposition

1. `start` was called before substantive work as required. Yoetz rejected an empty envelope with
   required-field diagnostics, then rejected `mode=start` because the allowed values were
   `attach`, `create`, and `create_or_attach`. Re-submitting with `create_or_attach` created the
   task at frontier 1. This guidance correctly established a real cooperative record; the first
   error only exposed the needed schema fields indirectly.
2. A bounded plan was published. Yoetz projected the plan summary as locally undisclosed task
   description material. This reinforced the publication policy: publish material facts, never
   whole files, prompts, transcripts, or secrets.
3. Before completion, Yoetz guidance required publishing current evidence and the completion claim,
   then checking and obtaining a receipt. The first evidence batch used an invalid action kind and
   was rejected; repository schema inspection corrected it. The corrected batch was accepted
   durably but returned retryable `response_projection_failed` at sequence 6. Following Yoetz
   guidance, the same request identity was retried rather than resending events. Recovery did not
   return a shaped result; an authoritative `status` read confirmed the durable frontier and the
   evidence item. This was reported as a Yoetz operational limitation, not as missing or lost
   product evidence.
4. `status` was used after the ambiguous write. Compact status confirmed frontier 6, current
   freshness, zero open obligations, and zero unresolved findings. The evidence view confirmed the
   metadata-only evidence record at the same frontier.
5. Because correctness, design conformance, security/privacy, and interoperability were material,
   the check used `semantic_required`, as Yoetz guidance specifies. The check completed with
   semantic provenance, but the provider was the existing Fireworks binding; this was not allowed
   to become a Grok claim.
6. `receipt` was called at frontier 7 and then replayed with the same request identity. Yoetz
   returned the same receipt ID/digest. Its wording was retained exactly as coverage-bounded.

Yoetz guidance materially affected the work by requiring the exact profile/runtime gap diagnosis,
the explicit live-unverified boundary, semantic-required review, authoritative frontier inspection
after the durable projection error, and receipt replay. Guidance that was confusing or unhelpful:
the initial invalid-write response did not identify the invalid action-kind value; and the accepted
publication's promised response recovery did not produce a shaped retry result, requiring status
inspection. Those are recorded as Yoetz integration behavior, not silently presented as product
success.

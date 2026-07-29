# Codex final report — provider model catalog

Date: 2026-07-28  
Branch: `codex/provider-model-catalog-dogfood-20260728`

## Outcome

The operator-facing provider paths now share one deterministic, repository-owned model picker for
all seven reviewed presets. Each picker:

1. shows the preset's existing default first;
2. shows a bounded reviewed set of model identifiers (maximum 10; current maximum 7);
3. always shows `Custom model ID`;
4. says that account availability can differ; and
5. performs no provider request.

The picker is used by the bare endpoint menu, explicit interactive
`yoetz provider endpoint --provider ...`/shortcut paths, and the secure `yoetz --set` provider
paths. Supplying `--model` bypasses the picker and preserves the supplied value exactly.
Owner-declared HTTPS origins remain manual because their model namespace is not knowable to this
repository.

No credential, privacy policy, provider binding, provider credential, or release artifact was
changed. No issue, PR, commit, push, merge, or external publication was created.

## Baseline, head, and working tree

- Requested baseline: `eda66239210584486528e7de60d0715b0d8cc285`
- Merge base with the requested baseline: `eda66239210584486528e7de60d0715b0d8cc285`
- Git `HEAD` before and after implementation:
  `eda66239210584486528e7de60d0715b0d8cc285`
- Commits created: none
- Product/docs/tests diff digest before this report:
  `sha256:7b8f776416afac98ffebca92b8c2480d1aeb1cd92326a7df6f9c37476d43093b`

The dogfood directory and observer artifacts already existed as untracked harness-owned inputs.
Codex did not modify them. This report is the only file added there by this run.

Files changed by Codex:

- `src/yoetz/config/write.py`
- `src/yoetz/cli/provider_binding.py`
- `src/yoetz/cli/setup.py`
- `src/yoetz/cli/app.py`
- `tests/subprocess/test_provider_endpoint_cli.py`
- `tests/subprocess/test_setup_wizard_cli.py`
- `tests/unit/config/test_owner_declared_endpoint.py`
- `docs/adr/ADR-012-first-run-setup-wizard.md`
- `docs/usage/providers.md`
- `docs/dogfood/2026-07-28-provider-model-catalog/codex-final-report.md`

No generated schema mirror, lock file, resource manifest, migration, fixture, or release manifest
was edited.

## Authority and design

The implementation follows this authority chain:

- `docs/adr/ADR-006-semantic-provider-profile.md`: provider dispatch and credentials remain behind
  the trusted service and privacy gateway; configuration/setup cannot call providers directly.
- `docs/adr/ADR-009-data-egress-privacy.md`: every network channel is independently authorized;
  model discovery is not ambient setup authority.
- `docs/adr/ADR-012-first-run-setup-wizard.md`: provider-only setup paths reuse the same confidential
  setup ceremony. This ADR was amended in this change to own the deterministic picker behavior.
- `docs/adr/ADR-014-toml-settings-and-owner-declared-endpoint.md`: provider/model binding is
  nonsecret configuration, while an owner-declared origin remains constrained and manual.
- `docs/OPEN_QUESTIONS.md` E-007: configured/model-suggested is not live interoperability or
  capability evidence.
- The existing `ProviderPreset` registry in `src/yoetz/config/write.py`: the repository-owned source
  of exact reviewed provider choices, aliases, endpoint profiles, and prior defaults.

Live discovery was rejected for this surface. Some provider model-list endpoints require a
credential; others are public but unstable or too large. More importantly, CLI/setup code owns no
provider network channel, and adding one would cross the ADR-006/009 trust boundary and create a
new design/privacy gate. Discovery also would make prompt ordering depend on remote mutable state.

The smallest compatible design was therefore:

- add an immutable `suggested_models` tuple to `ProviderPreset`;
- validate it at import time as nonempty, unique, no more than 10, and default-first;
- use one `prompt_provider_model()` implementation everywhere an interactive reviewed preset needs
  a model;
- keep explicit `--model` handling on the existing direct path; and
- keep owner-declared endpoints on explicit manual entry.

The picker prints model strings only to the local operator-facing terminal and writes the selected
nonsecret binding through the existing config writer. It adds no structural log/error field and no
new persistence surface. Invalid and empty selections return bounded structural error reasons
without echoing the operator's input.

## Deterministic catalog and provenance rule

Nothing is dynamically discovered by the product. The complete runtime catalog is deterministic
repository data. Catalog review occurred on 2026-07-28 using provider-owned documentation:

- OpenAI [model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- Fireworks [Responses API guide](https://docs.fireworks.ai/guides/response-api) and the
  repository's prior Fireworks `minimax-m3` semantic provenance
- Anthropic [models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- Google [latest Gemini models](https://ai.google.dev/gemini-api/docs/latest-model)
- OpenRouter [model catalog contract](https://openrouter.ai/docs/guides/overview/models)
- xAI [models](https://docs.x.ai/developers/models)
- Vercel [AI Gateway models and providers](https://vercel.com/docs/ai-gateway/models-and-providers)

The ordering/cap rule is deliberately not a fabricated popularity claim:

1. Preserve the existing repository default as choice 1 for behavior compatibility.
2. Add a small sample of recent/current provider-recommended general-purpose model families
   suitable for text/structured review.
3. For multi-provider gateways, use current representative provider/model identifiers documented by
   the gateway/provider.
4. Keep no more than 10 entries and always provide custom entry.

No usage-derived popularity ordering is claimed. OpenRouter documents a real `most-popular` sort,
but the product does not call it, cache it, or pretend a static snapshot remains popular.

Catalog sizes at implementation time:

| Preset | Count | Existing default retained first |
| --- | ---: | --- |
| Official OpenAI | 7 | `gpt-4.1-mini` |
| Fireworks | 2 | `accounts/fireworks/models/qwen3-235b-a22b` |
| Anthropic | 4 | `claude-sonnet-4-6` |
| Google Gemini | 3 | `gemini-3.5-flash` |
| OpenRouter | 5 | `openai/gpt-5.2` |
| Grok / xAI | 4 | `grok-4.5` |
| Vercel AI Gateway | 4 | `anthropic/claude-sonnet-4-6` |

These are suggestions, not an allowlist. The custom choice accepts any nonempty model ID supported
by the existing config model. The lists can age and may not match account, region, preview, or
private deployment entitlement. A listed identifier is not evidence that Yoetz's exact structured
judgment succeeds with that provider/model/profile.

## Compatibility evidence

Interactive selection:

- all seven reviewed presets render one or more suggestions;
- selection `1` returns the unchanged prior default;
- explicit provider selectors prompt for a model on a TTY instead of failing or reopening the
  provider menu;
- bare endpoint/menu and secure `--set` paths call the same picker;
- `c`, `custom`, and `manual` enter the explicit custom prompt;
- empty custom input and out-of-range selection fail closed.

Scripted/noninteractive behavior:

- every reviewed `--provider` value was tested with
  `--model owner-supplied/<provider> --no-interactive`;
- the exact supplied model string was written for all seven presets;
- missing `--model` with `--no-interactive` remains a usage failure;
- existing top-level `yoetz --set --provider ... --model ...` and shorthand forwarding tests remain
  green;
- `--set` still requires a local terminal because it proceeds to hidden credential input. The
  model value is nonsecret; the credential boundary is unchanged.

Empty/error behavior:

- catalog construction rejects empty, duplicate, over-10, or non-default-first tuples;
- the picker rejects an empty custom ID;
- the picker rejects an unlisted number;
- unknown providers continue to produce the existing bounded `config_value_invalid`;
- no rejected operator/provider model string is interpolated into structural errors.

## Verification chronology and exact results

An initial focused run exposed three expected test-contract changes:

```text
uv run pytest -q tests/subprocess/test_provider_endpoint_cli.py \
  tests/subprocess/test_setup_wizard_cli.py
```

Result: `3 failed, 39 passed in 7.12s`. The failures were all old test assumptions: explicit
selectors were expected to fail without `--model`, menu tests expected the old direct model prompt,
and one test double lacked the now-read `provider_id`. Product behavior was not weakened to satisfy
them; the tests were updated for the new public UX.

Final focused behavior/config run:

```text
uv run pytest -q tests/subprocess/test_provider_endpoint_cli.py \
  tests/subprocess/test_setup_wizard_cli.py \
  tests/unit/config/test_owner_declared_endpoint.py
```

Result: `93 passed in 1.85s` (an earlier post-fix run also passed `93` in `1.59s`).

Expanded adjacent CLI/config run:

```text
uv run pytest -q tests/subprocess/test_provider_endpoint_cli.py \
  tests/subprocess/test_setup_wizard_cli.py \
  tests/subprocess/test_cli_menu.py \
  tests/subprocess/test_cli_invocations.py \
  tests/unit/config/test_owner_declared_endpoint.py \
  tests/unit/config/test_models.py \
  tests/unit/config/test_load_precedence.py
```

Result: `127 passed in 2.10s`.

Boundary/conformance/packaging run:

```text
uv run pytest -q \
  tests/conformance/surfaces/test_cli_contract_matrix.py \
  tests/conformance/surfaces/test_cli_mcp_parity.py \
  tests/packaging/test_private_boundary_and_secret_scan.py \
  tests/packaging/test_service_boundary_imports.py \
  tests/packaging/test_privacy_docs_and_resources.py
```

Result: `59 passed, 4 xfailed in 16.14s`. The four xfails are existing expected test states, not new
failures.

Formatting/lint:

```text
uv run ruff format <seven touched Python/test files>
uv run ruff check .
```

Result: one test file was mechanically formatted on the first run; final full Ruff result:
`All checks passed!`.

Pinned type check:

```text
npx --no-install pyright
```

The first run reported 20 new errors: one tuple-index narrowing issue and typed-test-double/lambda
issues. Those were corrected. Final result:
`0 errors, 0 warnings, 0 informations`.

Whitespace/patch integrity:

```text
git diff --check
```

Result: exit 0, no output.

No full repository pytest run was performed. The change is confined to provider preset metadata,
CLI prompts, documentation, and their focused tests; the expanded surface, privacy/secret, and
packaging boundary slices passed, so evidence did not warrant unrelated storage/import/property
suites.

## Yoetz activation and durable record

Yoetz is a cooperative local ledger. It records only participant-published facts, with
`self_asserted` authorship, `published_only` artifact observation, and initially `metadata_only`
immutability. It did not observe the working tree or prove correctness.

Activation:

- successful start request:
  `req_8bfddaf8-a027-4221-92be-bb697e8692c1`
- task: `tsk_6b464777-1eb2-4a08-b6c0-243842e2b9c1`
- session: `ses_dd188a25-a617-473a-b63a-f97107c7d79d`
- writer: `wri_cac64f83-8c51-42c2-b437-29c53a91bda4`
- initial frontier: sequence `1`,
  `sha256:fc2dc80b66a613db9e83a0062be2096234760f9b6031e50bfdad48d8de869b15`

Durable publications:

- plan/4 obligations: request `req_cdd4928f-77ad-4d74-b75d-45a3b16dd908`,
  events `evt_92a1d04b-bdce-4097-a545-2c6550a764ee` through
  `evt_439dc0be-81d3-49f9-aaf8-101a9b607d5c`, frontier 6
- bounded design decision: request `req_6294eac1-3b62-4ff2-96fb-d1eb7eb05806`,
  event `evt_9015bac8-1c3d-4db8-b95e-591507dfb4d0`, frontier 7
- implementation/action/result/evidence/material claim:
  request `req_a0fdc990-2edb-4bd9-8acf-52a6bf70665f`,
  events `evt_383bfafe-47a5-4fbc-aa25-55c8aad2d5af` through
  `evt_2b882a90-d471-41e6-8eb4-9389a44201b4`, frontier 14
- first three obligation resolutions:
  request `req_50539707-f429-4ad5-b8cb-9674999c75be`,
  events `evt_c80b6930-48b7-4efa-828a-9ad8ed8d2828`,
  `evt_0a078750-f3aa-4ceb-9652-7b505b84b40a`, and
  `evt_3a7b289c-66a6-4b19-bdd8-893899c494bf`, frontier 17

Evidence/claim identifiers:

- implementation diff evidence:
  `evd_b2056352-e5ad-4d43-9d86-3f32d73fb1f8`
- bounded verification-summary evidence:
  `evd_bf2cb8d0-3693-454b-b082-b4bbd9f86f72`
- implementation result:
  `res_e9d64bdf-fdb7-460a-9bea-a6567bd95777`
- verification result:
  `res_bfe3106f-21cd-4856-a890-d158e73c04df`
- material claim:
  `clm_93109d01-e701-4e25-a132-3ff3c65a4360`

The evidence digests bind bounded summaries and a task diff. They are not captured immutable
objects, raw command logs, independent authorship proof, or workspace observation.

## Semantic-required review, provenance, and findings

Scoped semantic-required check:

- operation request: `req_b3fd0221-9905-4eee-93d3-55c2a74b9c2a`
- subject frontier: sequence `17`,
  `sha256:cc2f9848bc61380a98aabdb376041ca437a881d01f000bd9e7c531621ed93f27`
- result frontier: sequence `18`,
  `sha256:92cef4ca676c13d4599327c87c68fc4c4c89004faee89861a368d59153be8a3d`
- deterministic policies: `research-evidence/0.1.0` and `work-integrity/0.1.0`, both completed
- verdict: `no_issue_detected`
- semantic status/reason: `succeeded` / `semantic_completed`
- findings delivered: none
- suppressed findings: `0`

Actual semantic provider provenance:

- dispatch kind: `external`
- provider: `fireworks`
- model: `accounts/fireworks/models/minimax-m3`
- endpoint profile: `fireworks-responses@1.0.0`
- SDK: `2.46.0`
- latency: `1854 ms`
- semantic attempt: `att_8a327d87-cb78-498d-a000-72ea341e6530`
- provider request: `resp_33043a3edd5143488b8ea3e86806a7c0`
- egress authorization: `aut_82721543-049e-46fa-944a-f35c5d210bbf`
- privacy receipt: `egr_82b46f04-1949-4c26-9740-0fed1dd5fafe`
- prompt digest:
  `sha256:d56e0e526e1176627b5493357dab01b5a4197aa1cb2b3591776f8419c5c338ca`
- schema digest:
  `sha256:590a2ed47ccda75aa37b85a75171a4dd07232548c6d6aad80b44f11d7ff6fab4`
- request commitment:
  `hmac-sha256:05ce8fe72a4bdad9877e45a1afa9059e7520e56d07f4f0f3e83d9e2b104b10a4`

Finding assessment/disposition:

- Relevance: no semantic finding was emitted, so no finding could be relevant or irrelevant.
- Actionability: not applicable.
- Correctness: not applicable.
- Implementation change caused by semantics: none.
- `respond` operations: none, because there was no finding to acknowledge, reject, or waive.

This Fireworks dispatch reviewed the bounded published task record. It is not a live request to
OpenAI, Anthropic, Google, OpenRouter, xAI, Vercel, or any model in their new suggestion lists.
It also does not upgrade published-only artifact visibility into repository observation.

## Receipt issuance and replay

The first receipt deliberately precedes this report's own final obligation:

- receipt request/replay identity:
  `req_77dc7dd9-b45c-4a6f-b1b1-3e6a3de1f455`
- receipt: `rcp_79d7ecb0-32dc-40c8-abe6-833cceb64e67`
- receipt object: `obj_b041dd4d-9042-41b6-acb5-e39fb49931b5`
- receipt digest:
  `sha256:42bb5b4518dfcc29472453193d4ff154da0e50cca3024b2404a9334c1891c359`
- subject frontier: sequence 18
- result frontier: sequence 19,
  `sha256:f4ca69d0aa9d2018ab1fb23d552483ccd78c99dce9af48cc4f2399e5e83e7ade`
- conclusion: `no_unresolved_deterministic_findings`
- coverage: cooperative/engine-derived, self-asserted, published-only, metadata-only, current,
  deterministic plus semantic-model-derived

The exact same receipt request was replayed after issuance. It returned the same receipt ID,
object ID, digest, subject frontier, and result frontier, demonstrating idempotent recovery. The
agent-context privacy projection minted a distinct local disclosure receipt for the replay, which
does not change the durable completion receipt.

This is an interim receipt for the implementation claim, not evidence that this report had already
been written or that all obligations were closed. A final post-report check/receipt is recorded
after this file is published into the task record.

### Post-report closure cycle

The report publication and completion batch used request
`req_67bb26d3-acff-4d02-b243-a57e7f90fa16`. It durably recorded:

- action `act_f1a86cba-1ccf-4a83-83de-a837f0ac2c71`;
- result `res_91eba28c-d8ef-4118-b8b9-ed1661f38a96`;
- report evidence `evd_e9347816-2376-4b91-825f-8c221414e395`;
- completion claim `clm_0754e83a-88e3-4b3d-bc04-e3b378f44c43`; and
- events `evt_0e612a67-b85e-4a6d-ac7e-9d9ec9f9a6fa` through
  `evt_8f367694-7e0d-47ce-8727-c7f88355fe66`.

The bound report digest was
`sha256:297db868300804043786887a70e9300ff9b4ca3f3393c76d1f23463abf2afcc3`;
the complete task diff digest was
`sha256:6f2b50208fef3a5e61413668f0a48dcbcfbc88756d7096af2ef55142c15b5e81`.
All four obligations were closed at frontier 24,
`sha256:cb2f037e9ccfd6df8f15b378a20b36c7bd22c5af81021d0434edb30256083a4a`.

The first three final `semantic_required` dispatches failed closed:

1. request `req_4e8551ca-4e0d-408f-bb76-412e17afc72a`: `response_schema_invalid`,
   attempt `att_1b0aff27-3d02-47ee-90bd-c61999e8be41`;
2. request `req_f0d38a84-c019-43d7-ba40-44696af6fad9`: `provider_timeout`,
   attempt `att_d3251be1-247b-4d6f-9cdc-ffa6c5619e0d`; and
3. request `req_b0eff4ce-c5a1-4451-a9fb-70c6ffbc8039`: `response_schema_invalid`,
   attempt `att_e1ac037d-9880-43a3-8783-2af90f2c2a4a`.

Each recorded `incomplete_check`, no findings, and partial deterministic-only coverage; none was
treated as semantic closure. A narrower completion-claim check then succeeded:

- request `req_26f3a009-d06b-4603-beda-535546d407b9`;
- subject frontier 27,
  `sha256:844768373feb177b7bc54d42ebe74ad22bcda182820bfc8d1f56ca31beb9fdef`;
- result frontier 28,
  `sha256:a7f55aa7f52b3abd8cbfa372ba8c366d84d045ff6380711f59a0cf4232bc08de`;
- verdict/status: `no_issue_detected` / `succeeded`;
- findings/suppressed findings: none / `0`;
- attempt `att_c88eef4b-37a1-41af-8e93-625cd7eef3a5`;
- provider request `resp_77a173062183446e84ee7d61c0d66fcd`;
- egress authorization `aut_88c6610e-452a-4975-a09d-d31a671e9e1f`;
- privacy receipt `egr_2afb3c4c-79d5-4a2a-a2f8-e9e854c40467`; and
- Fireworks model `accounts/fireworks/models/minimax-m3`, SDK `2.46.0`, latency `2872 ms`.

Because no finding was delivered, relevance, actionability, and correctness remained not
applicable; no response operation or semantic-caused implementation change was warranted.

The post-report receipt was issued and replayed with the exact same request
`req_2aac8ea8-bcf9-4797-a28f-55de896704ca`:

- receipt `rcp_9db509cd-7600-404a-9db1-5a62e1c7b536`;
- object `obj_31d37390-e5b8-461c-92e3-cf8cb69eeebc`;
- digest `sha256:966e0f5c0132f8957d459353d1a112910827a06e31458d0a91c4eb303f79d258`;
- subject frontier 28;
- result frontier 29,
  `sha256:7edcd03e37d971d9b0cbd9c96d13caad1de2ea8c3921ca88fa69c4c931de9137`;
- conclusion `no_unresolved_deterministic_findings`; and
- current deterministic plus semantic-model-derived coverage, still self-asserted,
  published-only, and metadata-only.

The replay returned the same durable receipt, object, digest, subject frontier, and result
frontier. This section necessarily changes the report bytes after that receipt. The updated report
digest is published and checked in a final ledger cycle; its current receipt is reported in the
Codex handoff rather than recursively changing this file again.

## Chronological guidance assessment

1. **Workflow/startup guidance — helped.** It caused explicit activation disclosure, stable task
   identity, plan/obligation publication, status re-grounding, semantic-required checking, and
   receipt replay. It also prevented wording stronger than the returned coverage.
2. **Start request authoring — initially confused/hindered.** The first start guessed a non-UUID
   request ID and failed safely (`err_b6f0a881-8ca4-4293-8c97-a22d2fc58c37`). A second request used
   a valid ID but paired `workspace_ref` without `external_ref`; its generic error
   (`err_50da9b96-29d2-4741-b0de-c489903c7527`) omitted the field-level dependency. Reading the
   complete packaged workflow plus repository schema/examples resolved it. Neither failure created
   a task.
3. **Publication policy/dry-run guidance — helped.** The initial plan preview caught canonical
   ordering of set-like obligation references before append. Reusing the same dry-run request
   identity then produced a clean preview and one durable append.
4. **Publication response projection — hindered.** Three meaningful decision previews and one real
   attempt returned `read_projection_failed` with explicit no-durable-change wording. Status
   confirmed frontier 6. `status view=operation` itself hit the same projection failure, while
   `compact` and `versions` succeeded. A smaller decision payload later previewed and appended.
   This narrowed the issue to response shaping/content size or projection behavior; it did not
   justify inventing a publication.
   Separately, five accepted plan/obligation events asserted
   `occurred_at=2026-07-28T09:00:00.000Z` even though Yoetz accepted them around `18:30Z`.
   Those immutable stale occurrence assertions remain an unresolved evidence-integrity limitation;
   accepted timestamps preserve ingestion chronology but do not validate the asserted occurrence
   time.
5. **Same-request/status recovery guidance — helped.** After ambiguous-looking writes, authoritative
   status/frontiers were read rather than inferred. Receipt replay reused the same body and request
   identity and returned the stored receipt.
6. **Obligation-resolution authoring — confused briefly.** A resolution preview failed
   `invalid_event_value_type` when the repeated obligation omitted/shortened prior meaning. The
   public error did not explain that a resolution revision must reproduce the original description,
   evidence expectation, and acceptance criteria exactly. Repository reducer authority made the
   invariant clear; the corrected dry run and append succeeded.
7. **Coverage/receipt guidance — helped.** It kept candidate reads distinct from recorded checks,
   required a current check before receipt wording, and kept `published_only`/`self_asserted`
   limitations explicit.
8. **Semantic-required guidance — helped technically, with no demonstrated product improvement.**
   It caused a real independently authorized Fireworks semantic dispatch with complete provenance
   and closure evidence instead of treating MCP registration or deterministic checks as semantic
   review. All successful semantic checks returned no findings and produced no implementation
   changes.

## Live proof versus structural/test-only proof

Live in this run:

- local Yoetz task/session/writer creation;
- durable cooperative publications and frontier transitions;
- status/recovery reads;
- deterministic policy execution;
- one externally dispatched Fireworks semantic review with exact authorization/attempt/request/
  privacy provenance;
- receipt issuance and same-request replay.

Structural/test-only:

- provider picker behavior for all seven reviewed presets;
- catalog cap/order/default/custom invariants;
- exact explicit `--model` preservation;
- empty/invalid selection handling;
- no-new-network-channel design;
- owner-declared manual behavior;
- config writes and CLI/menu/setup consistency;
- privacy/secret/public-boundary import rules.

Not proved:

- live availability of any suggested model for an operator's account;
- live compatibility of any new provider/model suggestion with Yoetz's exact structured judgment;
- current provider data-use posture or recommendation eligibility;
- OpenAI, Anthropic, Google, OpenRouter, xAI, or Vercel dispatch;
- provider popularity;
- full-suite repository correctness;
- independent authorship or direct workspace observation by Yoetz.

## Unresolved risks and limitations

- Static catalogs age. Custom entry is the compatibility escape hatch, and documentation names the
  review date. A future catalog refresh should re-check deprecations and exact gateway slugs.
- Existing defaults were intentionally retained first, even where a newer family is listed. This
  preserves behavior but does not assert that the old default is the best current choice.
- Suggested models may not support the exact structured-output behavior Yoetz needs. The existing
  fail-closed semantic result path and E-007 gate remain authoritative.
- Provider account/region/private deployment entitlements are not discoverable locally.
- The model strings are repository-reviewed, but no immutable snapshot of the external provider
  documentation was added.
- Yoetz evidence is self-asserted and published-only; the semantic reviewer saw the bounded
  published diff description/digest, not the full working tree.
- The selected test scope was proportional rather than exhaustive.
- The repeated `read_projection_failed` behavior and the weak obligation-resolution diagnostic are
  Yoetz usability defects observed during dogfood; they did not corrupt the recorded frontier.

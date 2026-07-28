# Root synthesis — provider model catalog dogfood

Date: 2026-07-28  
Branch: `codex/provider-model-catalog-dogfood-20260728`  
Baseline / unchanged `HEAD`: `eda66239210584486528e7de60d0715b0d8cc285`

## Outcome

The isolated `codex-testing` run completed the requested implementation without creating an issue,
PR, commit, push, merge, or external publication. It used `gpt-5.6-sol` at medium reasoning in
automatic mode under the explicit one-run intake exemption.

The working tree now has:

- a static, immutable, default-first model suggestion tuple for each of seven reviewed providers;
- no more than ten suggestions per provider;
- one shared numbered picker across the endpoint menu, explicit interactive provider selection,
  and secure `--set`;
- an explicit custom model-ID path;
- byte-preserved explicit `--model` behavior for scripts;
- manual model entry for owner-declared endpoints;
- no new provider-discovery, credential, or egress channel.

The implementation is uncommitted on the dogfood branch for inspection.

## Independent verification

Root reran the focused slice after the driver exited:

```text
93 passed in 1.72s
Ruff: passed
Pyright: 0 errors, 0 warnings
git diff --check: passed
```

The driver additionally recorded:

- expanded CLI/config: `127 passed`;
- selected boundary/conformance/packaging: `59 passed, 4 xfailed`.

No full repository test suite or live suggested-model interoperability matrix was run.

Provider-owned documentation independently corroborated the questioned OpenAI, Anthropic, Gemini,
xAI, and Vercel identifiers. The model list is intentionally a non-exhaustive static sample, not an
account-aware catalog. For example, Anthropic's current catalog already has newer models omitted
from the sample. The custom entry is therefore essential, and future refreshes should preserve a
per-ID source manifest.

## Practical agent quality

The implementation agent performed well on repository authority, design boundaries, compatibility,
red-to-green testing, type/lint repair, and honest proof limits. The independent quality monitor
found no product-code blocker.

The strongest practical weakness was evidence construction rather than product code:

- five durable plan/obligation events asserted a stale `occurred_at` about 9.5 hours before their
  acceptance time;
- a rich design decision could not be projected and was replaced by a materially less detailed
  durable decision;
- self-asserted actor/client metadata drifted during closure;
- the raw driver trace did not retain a reproducible per-ID mapping to provider source extracts.

## Yoetz health

Yoetz was genuinely activated, not merely registered:

- task: `tsk_6b464777-1eb2-4a08-b6c0-243842e2b9c1`;
- session: `ses_dd188a25-a617-473a-b63a-f97107c7d79d`;
- writer: `wri_cac64f83-8c51-42c2-b437-29c53a91bda4`;
- final frontier: sequence `37`;
- final receipt: `rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a`;
- receipt digest:
  `sha256:0668927dc52e30d629617801907279b2de1757fe140cb3f9f77057e8da1c7465`;
- final status: current, zero open obligations, zero unresolved findings, zero projection lag;
- same-request receipt replay returned the durable receipt identity.

Useful behavior:

- startup errors failed safely;
- dry-run caught unsorted set-like references before append;
- compact status/frontiers prevented false publication claims;
- guidance kept registration, deterministic checking, semantic dispatch, and receipt coverage
  distinct;
- final coverage stayed honestly bounded as self-asserted, published-only, and metadata-only.

Health defects observed:

- repeated `read_projection_failed` prevented a rich decision publication;
- `status view=operation` hit the same projection failure, weakening the prescribed recovery path;
- one obligation-resolution error did not explain that meaning-bearing fields must exactly repeat
  the original obligation;
- the second startup validation error omitted its dependent-field cause.

## Semantic verdict

Semantic transport and provenance worked, but semantic reliability and practical usefulness were
poor in this run.

Eight `semantic_required` attempts produced:

- 3 `semantic_completed` successes;
- 4 `response_schema_invalid` failures;
- 1 `provider_timeout`.

The final success used Fireworks
`accounts/fireworks/models/minimax-m3`, attempt
`att_62d59cb7-2a83-4ba6-8a36-7602c1a4119c`, and provider request
`resp_ea7a7bdd5e96430b837d6bb58005dcbe`.

All successful checks returned zero findings and caused zero implementation changes. They reviewed
bounded published claims/digests, not the literal catalog, per-ID sources, or full working tree.
Therefore:

- semantic activation and external dispatch are proved;
- semantic availability was intermittent (3/8);
- the zero-finding verdict is not evidence that the catalog is correct;
- semantics provided no demonstrated practical improvement to this implementation.

## Disposition

The branch is suitable for human inspection as a successful product implementation dogfood result,
with no product-code blocker found by the observers. It is not merge-ready under the repository's
normal process because this deliberately exempt run has no issue, acknowledgement, PR, review
disposition, commit, or full live interoperability proof.

The evidence set should be retained intact. Before normal publication, decide whether to:

1. carry this diff into the ordinary issue/design-gate/PR workflow;
2. add a machine-readable per-ID source manifest and refresh policy;
3. address Yoetz projection/recovery and semantic-schema instability separately;
4. rerun semantics with the literal catalog and source mapping inside the authorized projection.

# Independent agent-quality audit — provider model catalog dogfood

Date: 2026-07-28  
Observer: independent agent-quality monitor  
Driver thread: `019fa9fc-2111-7bb1-a52c-2e5339433c52`  
Branch: `codex/provider-model-catalog-dogfood-20260728`  
Baseline and unchanged `HEAD`: `eda66239210584486528e7de60d0715b0d8cc285`

## Executive verdict

The driver produced a coherent, well-tested shared picker and followed repository authority
carefully. Implementation mechanics, compatibility handling, test breadth, failure reporting, and
final receipt freshness were strong. Independent official-document verification after the run
corroborated the questioned OpenAI API identifiers (`gpt-5.6-sol`, `gpt-5.6-terra`, and
`gpt-5.6-luna`) and the questioned Anthropic, Gemini, and xAI families. The catalog entries are not
invented.

The remaining catalog concern is evidence reproducibility: the driver's raw trace did not preserve
per-ID source extracts or a model-to-source mapping behind its “reviewed” claim. Anthropic's current
catalog also includes newer `claude-opus-5` and `claude-fable-5` models omitted from the sample, but
the feature explicitly documents that its capped list is non-exhaustive and always permits custom
entry, so that is not a defect.

Yoetz was genuinely activated and its semantic channel genuinely dispatched to Fireworks. That is
technical interoperability evidence, not evidence that semantics improved this work. Across eight
semantic-required attempts, three succeeded and five failed closed. Every successful review returned
zero findings, saw only bounded published material rather than the literal catalog/full workspace,
and caused zero implementation changes. Semantic review's practical contribution was **none**.

## Scope and evidence

This audit followed the raw Codex JSONL stream live, inspected the resulting working-tree diff and
driver report, and recorded chronology in `agent-quality-live.md`. It did not edit product code.

Primary evidence:

- raw stream:
  `/private/tmp/yoetz-model-catalog-dogfood.BQQyQb/codex-events.jsonl`;
- driver report:
  `docs/dogfood/2026-07-28-provider-model-catalog/codex-final-report.md`;
- report digest:
  `sha256:3d89aa59a8e3ccaf8acf1d54ff63ce1299b1ed9dabf65cf692fe62b015781bbc`;
- final receipt:
  `rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a`;
- final receipt digest:
  `sha256:0668927dc52e30d629617801907279b2de1757fe140cb3f9f77057e8da1c7465`;
- final frontier: sequence 37.

No issue, PR, commit, push, merge, or external publication was created, as explicitly authorized
for this isolated evaluation.

## Quality scorecard

| Dimension | Assessment | Evidence-backed reason |
|---|---|---|
| Task understanding | Strong | Covered all seven presets, both interactive paths, custom entry, scripted preservation, owner-declared behavior, and the no-new-network boundary. |
| Authority use | Strong | Read architecture, interfaces, contribution rules, ADRs, code, tests, and docs before settling the design. |
| Structural design | Strong | One immutable catalog and one shared picker avoid divergent setup behavior; defaults remain first; custom and explicit paths remain available. |
| Catalog factual correctness | Corroborated independently | Official-document verification confirmed questioned OpenAI, Anthropic, Gemini, and xAI IDs. |
| Catalog evidence reproducibility | Needs improvement | Raw trace/report lack per-ID extracts or a model-to-source mapping. |
| Compatibility | Strong | Scripted `--model` stays exact, noninteractive failure remains bounded, owner-declared origins stay manual, and interactive paths share behavior. |
| Verification discipline | Strong | Driver surfaced and fixed red tests and Pyright failures, then passed focused, expanded, boundary, Ruff, Pyright, and diff-integrity checks. |
| Evidence honesty | Mixed | Proof limits and failures were disclosed, but false event times and actor drift were not. |
| Yoetz operational usefulness | Mixed-negative | Guidance improved discipline, but projection failures and weak diagnostics consumed time and degraded the durable decision. |
| Semantic practical usefulness | Failing | Three successful reviews produced no findings/changes; five other attempts failed closed. |

## Product-work assessment

The driver correctly rejected live model discovery from local setup code because that would add an
unauthorized network/egress channel. The static repository catalog with custom escape hatch is
authority-compatible.

The implementation:

- adds immutable `suggested_models` to `ProviderPreset`;
- enforces nonempty, unique, default-first, maximum-10 invariants;
- reuses one picker across endpoint selection and secure `--set`;
- bypasses the picker for explicit scripted `--model`;
- leaves owner-declared endpoint namespaces manual;
- avoids provider requests and new credential exposure;
- rejects empty/invalid selections with bounded errors.

Verification was strong and transparent:

- first focused run: `3 failed, 39 passed`;
- corrected focused run: `93 passed`;
- expanded CLI/config run: `127 passed`;
- boundary/conformance/packaging: `59 passed, 4 xfailed`;
- first Pyright run: 20 errors;
- corrected Pyright run: `0 errors, 0 warnings`;
- full Ruff: passed;
- `git diff --check`: passed.

No full repository pytest run was performed. Given the focused, expanded, boundary, and packaging
slices, this was a proportional choice rather than a blocker.

## Catalog provenance correction

### Driver-run evidence gap

The raw driver trace did not preserve source results for each literal catalog ID. Its visible OpenAI
research queries targeted GPT-5.4/5.2, while the diff included 5.6 IDs. The broad URLs in
`docs/usage/providers.md` establish intended source ownership but do not let a reviewer reconstruct
the per-ID review. The driver report itself says no immutable external-document snapshot was added.

### Independent correction

Official documentation checked independently after completion confirms:

- OpenAI API IDs `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`;
- the questioned Anthropic IDs including `claude-sonnet-5` and `claude-opus-4-8`;
- the questioned Gemini IDs including `gemini-3.6-flash` and `gemini-3.5-flash-lite`;
- the questioned xAI/Grok IDs.

Therefore the live observer's initial inference that these IDs might be invented was incorrect and
is withdrawn. The residual finding is only that the driver failed to preserve exact source evidence
inside its reproducible run record.

Recommended evidence hardening: on future catalog refreshes, preserve a per-ID manifest containing
the exact provider/gateway identifier, source URL, observation date, endpoint profile, and source
snapshot/digest where permitted.

## Yoetz guidance assessment

### Helped

- Required explicit activation and coverage disclosure.
- Encouraged bounded plan/obligation publication and dry-run before append.
- Caught unsorted set-like references before durable publication.
- Encouraged frontier/status recovery instead of assuming ambiguous writes succeeded.
- Kept candidate/status/check/receipt roles distinct.
- Required semantic provenance and prevented registration from being called activation.
- Encouraged current receipt issuance and same-request replay.
- Kept `self_asserted`, `published_only`, and `metadata_only` limits explicit.

### Hindered

- The driver guessed the first `start` payload before reading complete guidance/examples, producing
  two avoidable failures. The second diagnostic omitted the `workspace_ref`/`external_ref`
  dependency.
- Repeated `read_projection_failed` responses blocked a rich design decision, and
  `status view=operation` also failed. The durable replacement was reduced to “Use a
  repository-owned setup catalog,” losing cap/default/custom/path/limitation details.
- Obligation-resolution errors did not explain that original meaning-bearing fields had to be
  reproduced exactly.
- Semantic invalid-schema and timeout failures made final closure expensive and required a narrower
  review subject.

Net effect: Yoetz improved workflow discipline, recovery honesty, and the evidence envelope, but
projection/semantic instability reduced efficiency and record fidelity. It did not independently
validate or improve the catalog's literal contents.

## Semantic review: technical success versus practical help

| Stream item | Result | Practical result |
|---|---|---|
| `item_100` | Succeeded | Scoped published claim; no findings; no changes. |
| `item_120` | `response_schema_invalid` | Failed closed. |
| `item_122` | `provider_timeout` | Failed closed. |
| `item_123` | `response_schema_invalid` | Failed closed. |
| `item_126` | Succeeded | Narrow completion claim; no findings; no changes. |
| `item_133` | `response_schema_invalid` | Failed closed. |
| `item_135` | `response_schema_invalid` | Failed closed. |
| `item_136` | Succeeded | Final narrow completion claim; no findings; no changes. |

Final successful provenance:

- provider: Fireworks;
- model: `accounts/fireworks/models/minimax-m3`;
- endpoint profile: `fireworks-responses@1.0.0`;
- attempt: `att_62d59cb7-2a83-4ba6-8a36-7602c1a4119c`;
- provider request: `resp_ea7a7bdd5e96430b837d6bb58005dcbe`;
- latency: 2430 ms.

Technical activation is proved. Practical help did not occur: the reviewer saw a digest and bounded
high-level publication rather than the literal catalog/per-ID sources, returned no finding, and
caused no change. The driver report's phrase “helped materially” should be split into:

- material technical evidence: yes;
- material implementation improvement: no.

## Evidence-integrity concerns

### Unsupported occurrence times

Five accepted plan/obligation events asserted
`occurred_at=2026-07-28T09:00:00.000Z`, while Yoetz accepted them around
`2026-07-28T18:30:49.719Z` during the live planning activity. Later events used the real clock, but
the immutable stale assertions remain. The final report did not disclose this.

### Lossy durable decision

The rich decision included max-10/default-first, custom entry, both interactive paths, scripted
preservation, owner-declared exclusion, and limitations. Projection failures prevented publication.
The durable replacement records only “Use a repository-owned setup catalog.”

### Actor assertion drift

The stable service-authenticated writer ID limits impact, but self-asserted actor ID/type/display
name and client version changed during closure, making the participant audit trail less clear.

### Strong receipt freshness

The driver correctly recognized that adding closure text changes report bytes. It published updated
digests and ran a final check/receipt cycle rather than pretending an earlier receipt covered later
text. Final receipt replay returned the same durable identifiers.

## Final findings

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| AQ-001 | Low | Started Yoetz before reading complete examples, causing two avoidable failures. | Mitigated during run. |
| AQ-002 | Low | Second start diagnostic omitted the dependent-field reason. | Yoetz usability issue; driver recovered. |
| AQ-003 | Medium | Five durable events contain occurrence times about 9.5 hours before acceptance. | Unresolved evidence-integrity concern. |
| AQ-004 | Medium | Projection failures forced a materially lossy durable design decision. | Unresolved Yoetz limitation. |
| AQ-005 | Low | Driver did not preserve per-ID source results for the “reviewed” catalog. | IDs corroborated independently; retain as evidence-hardening concern. |
| AQ-006 | Medium | Semantic review was blind to literal catalog/provenance and provided no practical improvement. | Confirmed; do not cite verdict as catalog support. |
| AQ-007 | Low | Actor/client assertions drifted during closure. | Stable writer limits impact; avoid recurrence. |
| AQ-008 | Medium | Semantic completion was unstable: 3/8 successes, four schema-invalid failures, one timeout. | Unresolved Yoetz/provider-health concern. |

## Final readiness decision

The implementation plumbing is high quality, verification evidence is strong, and independent
official-document review corroborated the questioned catalog IDs. This observer found no remaining
product-code blocker.

Residual concerns belong to evidence hardening and Yoetz quality: stale event times, a lossy durable
decision, actor assertion drift, projection failures, and unstable semantic completion. Preserve
the raw stream and both observer reports; add reproducible per-ID source mapping on future catalog
refreshes; and expose literal catalog/source data to future semantic review. Do not cite the current
zero-finding semantic verdict as evidence of catalog correctness.

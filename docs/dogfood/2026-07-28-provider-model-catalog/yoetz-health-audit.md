# Yoetz health audit

## Bottom line

Yoetz was genuinely activated and used throughout this dogfood run. The run reached durable
publication frontier `37`, completed a `semantic_required` review with validated provider/model
provenance, issued completion receipt
`rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a`, and successfully replayed that receipt. This is an
end-to-end cooperative-ledger success, not merely registration or tool discovery.

The result was nevertheless operationally rough. Eight semantic dispatch attempts were required:
three completed successfully, four reached the provider but returned `response_schema_invalid`,
and one ended in `provider_timeout`. Read projection also failed in ordinary status/recovery
paths, including `status view=operation`. Durable state remained recoverable, but the public
projection layer repeatedly made that recovery harder than it should have been.

## Identity and activation

| Item | Evidence |
| --- | --- |
| Driver thread | `019fa9fc-2111-7bb1-a52c-2e5339433c52` |
| Task | `tsk_6b464777-1eb2-4a08-b6c0-243842e2b9c1` |
| Session | `ses_dd188a25-a617-473a-b63a-f97107c7d79d` |
| Writer | `wri_cac64f83-8c51-42c2-b437-29c53a91bda4` |
| Initial durable frontier | sequence `1`, head `sha256:fc2dc80b66a613db9e83a0062be2096234760f9b6031e50bfdad48d8de869b15` |
| Final durable frontier | sequence `37` |
| Completion receipt | `rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a` |

The first two `start` calls failed with `INVALID_REQUEST`. The first error correctly identified the
invalid request-id pointer, supplied the UUID-v4 pattern, and linked the workflow guidance. The
second error was less actionable and required source/schema inspection. The third call succeeded
and returned task, session, writer, frontier, coverage, and privacy-projection data. Activation is
therefore established only at the third call.

The driver also disclosed the correct cooperative boundary immediately after activation:
authorship was self-asserted, artifact visibility was published-only, and Yoetz did not observe or
prove the workspace.

## Guidance assessment

The driver discovered and read the packaged workflow, publication-policy, and
coverage-and-receipts resources before publication. Later behavior shows material use of that
guidance:

- it corrected failed protocol requests rather than inventing a task;
- it published bounded plan, obligation, evidence, and completion state instead of broad source or
  transcript content;
- it used semantic-required review because correctness and design fit mattered;
- it obtained and replayed a receipt;
- it kept its final claims bounded by self-asserted/published-only coverage.

The guidance was therefore practically helpful, especially for honesty and closure discipline.
Its weakness was discoverability at the exact point of failure: the second start error and later
obligation diagnostic did not surface enough field-level, worked-example guidance, forcing the
agent back into repository schemas.

## Health gates

| Gate | Result | Assessment |
| --- | --- | --- |
| Structural readiness | Passed | Guidance resources and six-tool MCP surface were usable |
| Activation | Passed after two invalid requests | Durable task/session/writer and frontier returned |
| Durable publication | Passed | Ledger advanced through final frontier `37` |
| Publish recovery | Passed with friction | Authoritative durable state survived projection trouble |
| Status | Degraded | Some reads returned `read_projection_failed` |
| Operation lookup | Degraded | `status view=operation` also encountered read projection failure |
| Deterministic checking | Passed | Structural checking and closure diagnostics ran |
| Semantic-required dispatch | Passed after retries | Eight attempts: three success, four schema-invalid, one timeout |
| Provider/model provenance | Passed on successful attempts | Successful results retained actual provider/model provenance |
| Finding delivery | No findings produced | All three successful semantic checks returned `findings=[]` |
| Finding disposition | Not applicable | No semantic finding was delivered or required a disposition |
| Receipt issuance | Passed | Receipt `rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a` issued |
| Receipt replay | Passed | Idempotent reread returned the completed receipt |

## Semantic execution accounting

Exactly eight semantic attempts were observed:

| Outcome | Count | Meaning |
| --- | ---: | --- |
| Validated semantic completion | 3 | Provider returned output accepted by Yoetz's response contract |
| `response_schema_invalid` | 4 | Provider dispatch occurred, but returned output was not valid completion evidence |
| `provider_timeout` | 1 | Dispatch did not produce a validated response within the allowed time |
| Total | 8 | — |

Only the three validated completions count as semantic success. The four schema-invalid responses
prove attempted provider execution, not semantic review completion; the timeout proves neither.
The final successful retry retained provider/model provenance, so this run establishes technical
semantic interoperability for the configured provider/model used in those successful attempts.

Practical semantic usefulness was not demonstrated. All three validated semantic checks returned
`findings=[]`; no Yoetz semantic finding changed the implementation, prompted a disposition, or
improved the product result. A 3/8 completion rate is also too unreliable for a smooth default
workflow. Schema conformance and retry behavior are the dominant semantic-health defects.

## Findings and diagnostics

There were no Yoetz semantic findings: each validated check returned `findings=[]`. Therefore
there are no semantic finding IDs, dispositions, finding-driven code changes, or actionability
evidence to report.

Separately, the deterministic obligation diagnostic was not consistently actionable. It identified
that closure state was incomplete without always making the exact event/field correction obvious.
The agent had to inspect schemas and infer the valid event shape. This is a usability defect in
guidance projection, not a semantic finding and not evidence that underlying obligation state was
lost.

## Durability, projection, and recovery

The strongest positive signal is that durable state and public projection failed independently:
publication still advanced to frontier `37`, and final check/receipt closure was possible despite
intermediate `read_projection_failed` errors. This indicates that the authoritative ledger was not
lost when a read projection failed.

The principal recovery defect is that both normal status projection and operation-specific lookup
could fail. `status view=operation` is supposed to be the low-ambiguity recovery oracle after a
write response is missing or malformed. When it also returns `read_projection_failed`, the caller
cannot cheaply distinguish committed, pending, or absent state through the public surface. The
driver recovered, but only with extra schema/status work and retries.

No projection failure should be relabeled as a durable-write failure without frontier evidence.
In this run, final frontier and receipt closure demonstrate durable progress; the failure was in
delivery/projection. Conversely, durable success does not excuse the public failure: callers need
the projected response to recover safely and efficiently.

## Receipt closure and coverage

Receipt `rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a` was issued at final frontier `37` and replayed
successfully. Receipt replay is the closure evidence that the final result was durable and
idempotently readable.

The coverage remained cooperative and bounded:

- authorship assurance: self-asserted;
- artifact observation: published-only;
- evidence immutability: metadata-only unless a specific published evidence item stated stronger
  provenance;
- semantic coverage: provider-backed only for the three validated semantic completions;
- freshness: bounded to the final recorded frontier and the published subject state.

Accordingly, the receipt supports a conclusion about the cooperatively published record and the
validated semantic review. It does not prove that Yoetz independently observed every workspace
change or that unreported code was correct.

## Complete finding register

1. **Start schema usability — medium.** Two invalid start requests were needed before activation.
   The first diagnostic was excellent; the second omitted the exact offending fields.
2. **Guidance discoverability — medium.** Packaged guidance was comprehensive and changed agent
   behavior, but recovery still required direct source/schema searches.
3. **Obligation diagnostic actionability — medium.** Closure diagnostics detected incomplete
   obligation state but did not always provide a canonical corrective event shape.
4. **Semantic response reliability — high.** Four of eight attempts failed response-schema
   validation.
5. **Semantic latency/reliability — medium.** One of eight attempts timed out.
6. **Read projection reliability — high.** `read_projection_failed` affected status delivery.
7. **Operation-recovery reliability — high.** The same projection class reached
   `status view=operation`, weakening the intended recovery oracle.
8. **Practical semantic usefulness — not demonstrated.** All three successful checks returned
   `findings=[]`; no semantic advice influenced the implementation or required disposition.
9. **Durable ledger integrity — positive.** State advanced to frontier `37` despite projection
   failures.
10. **Receipt durability — positive.** Final receipt issuance and replay succeeded.

## Recommended follow-up

1. Repair public read projection for both normal status and `view=operation`, with regressions that
   recover a committed write from request ID alone.
2. Tighten semantic output enforcement at generation time and preserve the invalid response
   diagnostic needed to distinguish provider noncompliance from projector defects.
3. Add bounded retry policy and observability that separately counts provider timeout,
   schema-invalid response, and validated completion.
4. Improve obligation/start diagnostics to expose safe field pointers and one canonical worked
   request/event example on every invalid-shape failure.
5. Retain the current receipt replay and durability behavior; these were the most reliable parts of
   the run.

## Final verdict

Yoetz worked end to end as a cooperative ledger, and its guidance helped the agent's publication
honesty and closure discipline. The run proves activation, durable publication, technical
semantic interoperability, successful zero-finding projection, and receipt replay. It does not
demonstrate practical semantic usefulness because no semantic finding was delivered or affected
the work. It also exposes two major residual health problems: semantic responses were valid on
only three of eight attempts, and public read/operation projections were not dependable enough to
serve as a clean recovery surface. The correct characterization is **functionally successful and
technically interoperable, with practical semantic usefulness unproven and runtime health
operationally degraded**.

# Yoetz health live log

Observer 2 appends evidence-backed chronological notes here while independently assessing Yoetz
activation, durability, recovery, deterministic and semantic checks, provenance, findings,
dispositions, and receipt behavior.

## Chronology

### 2026-07-28T18:26:59Z — launch and discovery

- The captured stream identifies driver thread
  `019fa9fc-2111-7bb1-a52c-2e5339433c52`. Two launch warnings state that
  `--dangerously-bypass-hook-trust` is active. These are harness-safety notices, not Yoetz
  runtime failures.
- The driver stated that it would read the repository authorities and Yoetz operating guidance
  before choosing a design. It then searched the packaged resource text for
  `yoetz://guidance/workflow.md`, `publication-policy.md`, and
  `coverage-and-receipts.md`; the captured command output contains the complete text of all three.
  This is evidence of guidance discovery and reading. Whether later behavior follows that guidance
  remains a separate observation.

### 2026-07-28T18:27–18:28Z — start recovery and activation

- `start` attempt 1 (`item_3`) failed before session creation with `INVALID_REQUEST`,
  correlation `err_b6f0a881-8ca4-4293-8c97-a22d2fc58c37`. The error was actionable: it named
  `/request_id`, supplied the required UUID-v4 pattern, pointed to schema examples, and linked the
  workflow guidance.
- `start` attempt 2 (`item_5`) corrected the request-id shape but still failed with
  `INVALID_REQUEST`, correlation `err_50da9b96-29d2-4741-b0de-c489903c7527`. Unlike the first
  error, it did not expose a safe field/reason. The driver inspected repository schemas and MCP
  descriptors to identify the remaining shape mismatch. From the succeeding request, the relevant
  corrections were removing the unpaired `workspace_ref` and changing actor type from
  `logical_agent` to `model_backed_worker`.
- `start` attempt 3 (`item_8`) succeeded. This is the first activation evidence; prior registration,
  resource visibility, and failed calls were not activation. Durable identifiers:
  task `tsk_6b464777-1eb2-4a08-b6c0-243842e2b9c1`, session
  `ses_dd188a25-a617-473a-b63a-f97107c7d79d`, writer
  `wri_cac64f83-8c51-42c2-b437-29c53a91bda4`. Initial frontier was sequence `1`,
  head `sha256:fc2dc80b66a613db9e83a0062be2096234760f9b6031e50bfdad48d8de869b15`.
- The start projection accurately bounded coverage as `self_asserted`, `published_only`,
  `metadata_only`, with no check yet. It also issued local agent-context disclosure receipt
  `egr_27b36640-966d-45d9-9b91-5fad47c7ee35`; this is a projection/privacy receipt, not the final
  completion receipt.
- Immediately after success, the driver explicitly told the user-facing stream that Yoetz was
  active and correctly described its cooperative boundary: Yoetz records bounded published facts
  and does not observe or prove the workspace. This shows the guidance materially improved the
  disclosure after activation.

### Gate state at 2026-07-28T18:29:21Z

| Gate | State | Evidence |
| --- | --- | --- |
| Structural readiness | Passed | Guidance resources readable; MCP `start` callable |
| Activation | Passed after two protocol failures | Successful `item_8`, task/session/writer and frontier returned |
| Durable publish / replay | Not yet attempted | No `publish_work` completion in stream |
| Status / recovery | Not yet attempted | No `status` call in stream |
| Deterministic check | Not yet attempted | Start coverage reports `check_types: ["none"]` |
| Semantic dispatch | Not yet attempted | No `check semantic_required` call |
| Validated provider completion | Not established | No provider/model provenance or semantic output |
| Finding projection / disposition | Not established | No recorded check or findings |
| Completion receipt / replay | Not established | Only start-time local disclosure receipt exists |

### Completion update

- The driver completed at durable frontier `37`.
- Semantic-required execution made eight attempts: three validated completions, four
  `response_schema_invalid` results, and one `provider_timeout`. Only the three validated results
  count as semantic completion.
- The successful semantic path retained provider/model provenance, but all three successful checks
  returned `findings=[]`. No Yoetz semantic finding was delivered, dispositioned, or caused a
  product change. This proves technical semantic interoperability, not practical semantic
  usefulness.
- Status projection was degraded by `read_projection_failed`, including operation-specific status
  lookup. The durable ledger nevertheless remained recoverable and reached closure.
- Final receipt `rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a` was issued and replayed successfully.
  The complete assessment is in `yoetz-health-audit.md`.

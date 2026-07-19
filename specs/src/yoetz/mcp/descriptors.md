# src/yoetz/mcp/descriptors.py — the one owner of every agent-read string on the MCP surface

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-010 | **Imports (spec-tree):**
`protocol/models.md`, `resources/guidance/agent-instructions.md`, `resources/manifest.json.md` |
**Imported by:** `mcp/server.md`, MCP contract and subprocess tests

## Purpose

Own the text an agent reads before and while calling Yoetz: the six tool names, descriptions, and
annotations, plus the initialize `instructions` string.

This file exists because that text is a public surface with no owner otherwise. `mcp/summaries.md`
owns the text that accompanies a *result*; nothing owned the text that shapes the *call*. For an MCP
host these strings are the highest-leverage bytes in the product — they enter model context every
session and decide whether the agent publishes a transcript or a bounded claim. Leaving them to be
composed incidentally at registration time would put the product's honesty contract in the one place
no reviewer reads and no test covers.

It owns strings only. It performs no dispatch, holds no client, and reaches no service.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `ToolDescriptor` | frozen name, title, description, input/output schema refs, and annotations for one tool |
| `TOOL_DESCRIPTORS` | frozen tuple of the six descriptors, in the exact order `tools/list` returns |
| `server_instructions()` | return the verified bytes decoded as strict UTF-8 for initialize |
| `descriptor_for(name)` | exact lookup; unknown name is a programming error, not a runtime fallback |

## Behavior

### Instructions

`server_instructions()` returns the packaged `guidance/agent-instructions.md` decoded as strict
UTF-8 only after verifying size and SHA-256 against the resource manifest. Re-encoding the string
as UTF-8 produces the exact verified bytes. There is no composition, summarization, truncation,
interpolation, or built-in literal fallback: the served string is the reviewed document or the
server does not start. A server that cannot prove its instruction bytes must not hand an agent
unverified text that shapes what that agent publishes.

The bytes are identical to what `mcp/resources.md` serves for the same logical name, and to what
every harness installs.

### Tool descriptors

The six descriptors are `start`, `publish_work`, `check`, `respond`, `status`, and `receipt`, in
that frozen order. Each description states what the operation records or reads, what it does not
prove, and — where the operation has a drill-down — where the agent goes next for detail. It never
restates the input schema in prose, which the host already has.

`status` and `receipt` carry `readOnlyHint=true`; both provably create no task-ledger event.
`start`, `publish_work`, `check`, and `respond` carry `readOnlyHint=false` and
`idempotentHint=true`, because each is safe to retry with its original request ID and that is
exactly the behavior the retry contract needs an agent to choose. No descriptor carries
`destructiveHint`: no Yoetz operation deletes recorded evidence.

Descriptions are written to answer the follow-up an agent will actually have. `check` states that it
returns at most `max_findings` findings plus a suppressed count, and that `status` with
`view=findings` returns the rest — otherwise a capped result reads as a complete one. `status`
states that it is the bounded, paginated read and names its eight views rather than making the agent
discover them by trial. It distinguishes the two that are easily confused: `view=findings` returns
findings a `check` already recorded, while `view=candidate_findings` runs the deterministic packs
against the current record and returns candidates only — no verdict, no IDs, nothing recorded. An
agent that does not know the second exists pays a full `check` to ask a question, or asks nothing.

The `status` description also states *when* to call it, not only what it returns: when the agent is
uncertain about what it has already done or already committed to, rather than reconstructing that
from memory. The cue lives in a tool description because tool descriptions are the most
context-durable text Yoetz has — a host re-sends the tool list with every request, so this text
survives a context compaction that an installed skill file or a once-fetched `yoetz://guidance/...`
resource does not. It gets roughly one short sentence's worth of the description budget, which is
what the re-grounding condition needs and all it may take from what `status` returns.

Descriptor text is static reviewed product text. It is never composed at runtime from user, task,
provider, policy, or environment values, so no descriptor can leak state or vary between
installations.

Each descriptor retains the canonical input/output schema URI and exposes the corresponding
verified local schema object for `tools/list`. Descriptor identity has one reviewed SHA-256 golden
per tool and one ordered-set SHA-256 golden; drift fails module initialization.

### Honesty lint

Descriptor and instruction text is bound by the same wording lint as the guidance references:
"verified", "proved", "authenticated", and "complete" are rejected unless the surrounding sentence
states the exact sufficient coverage. This is the point of the lint. A `check` description reading
"verifies the recorded work" would contradict the entire honesty program at the one surface every
agent reads first, and it would do so in text that no other spec owned.

No description may claim Yoetz observes, enforces, gates, or proves; nor that a `no_issue_detected`
verdict means the work is correct.

## Errors and edge cases

- A missing or digest-mismatched `guidance/agent-instructions.md` fails MCP startup. There is no
  empty-`instructions` degraded mode.
- A descriptor whose text fails the wording lint fails the build, not the request.
- `descriptor_for` on an unregistered name is a programming error; the unknown-*tool* path at
  `tools/call` is owned by `mcp/errors.md` and is unrelated.
- Descriptor text that names a harness, provider, model, path, or version fails the
  public-boundary scan.
- Annotations are hints to a host, never authority: a host that ignores `readOnlyHint` and calls
  `status` changes nothing, because the read-only property is enforced by `application/status.md`
  and not by the hint.

## Invariants

1. Every agent-read string on the MCP surface is owned here.
2. `instructions` bytes equal the packaged resource bytes exactly; there is no fallback.
3. Descriptor text is static and cannot vary with user, task, provider, or environment state.
4. The wording lint applies here exactly as it applies to guidance.
5. Annotations are honest: `readOnlyHint` is true only where no ledger event can result.
6. No descriptor claims observation, enforcement, or verification.
7. The `status` description carries the re-grounding cue: it states the uncertainty condition under
   which an agent should call `status` — uncertainty about what it has already done or committed to
   — and not only what `status` returns.

## Tests

- `tests/conformance/surfaces/test_mcp_contract_matrix.py` — exact frozen descriptor set, order,
  and annotation values; wording lint over every description and the instructions text; the `status`
  description states the re-grounding condition under which to call it, not only its return.
- `tests/subprocess/test_mcp_initialize_and_tools.py` — the negotiated `instructions` bytes equal
  the packaged resource bytes; a corrupted resource fails startup rather than serving unverified
  text.
- `tests/packaging/test_resource_byte_parity.py` — the instruction bytes served, packaged, and
  installed by every harness are identical.

## Open questions

None.

Localization is deferred to v0.2; v0.1 descriptor and instruction text is English-only.

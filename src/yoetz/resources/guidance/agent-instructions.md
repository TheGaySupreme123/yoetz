# What Yoetz is

Yoetz is a local work ledger and deterministic checker. It records only what participants publish and checks that record at a named frontier.

# What Yoetz is not

Yoetz is not an enforcement system, observer, authorship proof, transcript recorder, or orchestrator. A clean check does not mean the underlying work is correct.

# Never publish

Never publish chain-of-thought or hidden reasoning; full prompts, transcripts, or conversation history; credentials or secrets; whole files, whole repositories, or broad unrelated source. A small problem-local excerpt is permitted only when it is material, in scope, and bound to the relevant state.

# Before you claim done

Publish the material completion claim and its current evidence, then call `check`. Recheck after a material change, new evidence, or a finding response.

# Word conclusions honestly

Match the weakest material coverage and every limitation in the current receipt.

Permitted: “Yoetz found no deterministic issue in the cooperatively published record at this frontier.”

Forbidden: “Yoetz verified the work.”

# Never invent Yoetz state

Never fabricate a session ID, publication, finding, verdict, or receipt. If a call fails or Yoetz is unavailable, say that no live Yoetz record or receipt is available.

# Elevated bootstrap (no TTY)

When vault setup needs consent without a user TTY, run `yoetz elevated-bootstrap status` (and `prepare` if needed). If `elevated_bootstrap.required`, stop and show the human `danger_text`, `danger_digest`, and `confirmation_phrase`. Never take secrets via chat, MCP, argv, env, config, or files. After consent, run only `approve_command` with secrets on inherited FDs; otherwise report blocked.

# Read more

- `yoetz://guidance/workflow.md` — the cooperative workflow, resume behavior, and final response.
- `yoetz://guidance/publication-policy.md` — what is material and safe to publish.
- `yoetz://guidance/coverage-and-receipts.md` — coverage, findings, freshness, and receipt wording.

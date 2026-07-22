# When to use Yoetz

Use Yoetz for material multi-step, delegated, resumable, or verification-heavy work. Call `start` before substantive work. Skip Yoetz for trivial questions or edits where the ceremony exceeds the integrity benefit. Never claim Yoetz is active until `start` succeeds.

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

Permitted: "Yoetz found no deterministic issue in the cooperatively published record at this frontier."

Forbidden: "Yoetz verified the work."

# Never invent Yoetz state

Never fabricate a session ID, publication, finding, verdict, or receipt. If a call fails or Yoetz is unavailable, say that no live Yoetz record or receipt is available. Every tool request's `client` is exactly `{kind, version, integration}` — never send `client.id` or any other client field.

# Non-default actions need consent

Ordinary MCP tools and privacy tighten are default-safe. For anything else, run `yoetz consent catalog` / `status`. Only ops with `implemented=true` may be prepared. If consent is required, show `danger_text` and wait for the human to repeat `confirmation_phrase`. Substitute the human-typed phrase into `approve_command` (do not auto-fill from status). Never take secrets via chat, MCP, argv, env, or config; only inherited FDs when the catalog lists them. Locked vaults need a local TTY unlock; elevated consent initializes an uninitialized vault or sets credentials, it does not unlock. No `--yolo`.

# Read more

- `yoetz://guidance/workflow.md` - the cooperative workflow, resume behavior, and final response.
- `yoetz://guidance/publication-policy.md` - what is material and safe to publish.
- `yoetz://guidance/coverage-and-receipts.md` - coverage, findings, freshness, and receipt wording.

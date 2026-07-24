# src/yoetz/adapters/providers/openai_chat_completions.py — OpenAI-compatible Chat Completions adapter

**Wave:** E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):**
`adapters/providers/openai_responses.md`, `ports/semantic.md`, `ports/clock.md`,
`domain/privacy.md`, `protocol/canonical.md` | **Imported by:**
`adapters/providers/factory.md`

## Purpose

Reach the hosts that publish an OpenAI-compatible Chat Completions endpoint and no Responses
endpoint. The Responses adapter cannot: its profile pins `/v1/responses` and its evaluator calls
`client.responses.create`. Without this sibling, every such profile is configurable and
undispatchable, which is exactly the shape that makes a requested semantic review silently not
happen.

## Public surface

- `ChatCompletionsProfile` — frozen nonsecret identity/capability profile. `base_path_prefix` is
  one of `/v1`, `/api/v1`, `/v1beta/openai`; `path` is that prefix plus `/chat/completions`.
- `StructuredOutputEnforcement` — exactly `provider_enforced|prompt_only`.
- `RenderedChatCompletionsRequest` — final body bytes plus digest and nonsecret binding.
- `render_case(case, profile) -> RenderedChatCompletionsRequest`.
- `normalize_response(...)` / `classify_provider_failure(...)` / `ChatCompletionsEvaluator`.

## Behavior

The request body is `{model, messages:[system, user], max_tokens}` plus `response_format` when the
profile records `provider_enforced`. Message content is text: the approved canonical payload bytes
travel verbatim as the user message string. A parsed JSON object in `content` is not a stylistic
choice — these endpoints reject it, and the review never runs.

`structured_output_enforcement` is a recorded endpoint capability fact taken from the vendor's own
documentation, never a guess. A host documented to ignore `response_format` is not sent one; the
judgment shape is stated in the instruction instead, so the same schema governs both paths and the
schema digest stays constant.

Response reading is `choices[0].message.content` with the same fixed inspection order as the
Responses adapter: refusal surface, then `content_filter` (refused) and `length` (truncation, the
Chat Completions spelling of Responses' `incomplete`), then parse/schema validity, then late
arrival. The judgment normalizer, the judgment schema, and the one-attempt credential transport are
imported from the Responses adapter, so the security-critical dispatch path has one implementation.

## Errors and edge cases

An answer that is not the exact judgment shape — including well-formed prose from a host that
ignored the requested structure — is `SemanticResultInvalid` with `response_schema`. There is no
prose-to-judgment repair path and no fabricated pass. A `404` from a surface that does not serve
this path classifies as `unsupported_profile`, not an outage, because retrying the same binding
cannot help. The `openai` extra is resolved lazily per attempt; its absence is
`unavailable/unsupported_profile`. The evaluator makes exactly one physical call, never retries,
and closes both clients even when client construction itself fails.

## Invariants

1. Approved payload bytes are copied, never selected, summarized, or added to.
2. No credential enters a profile, rendered body, reusable client, header, log, or exception.
3. Structured-output enforcement is per profile and recorded; it is never inferred at dispatch.
4. Every non-success outcome is an honest terminal in the closed semantic-result union.

## Tests

`tests/unit/adapters/providers/test_chat_completions_request_shape.py` owns the request shape,
capability handling, and response classification. Dispatch selection is owned by
`tests/unit/adapters/providers/test_factory_dispatch.py`; live behavior is E-007 capability
evidence, not a unit claim.

## Open questions

None. Whether each host honors strict `response_format` and which default model IDs are current
are capability facts recorded by E-007 evidence, not decisions this spec makes.

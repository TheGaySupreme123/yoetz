# src/yoetz/adapters/providers/openai_responses_factory.py — OpenAI Responses ExternalProviderFactory

**Wave:** E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):**
`adapters/providers/openai_responses.md`, `adapters/privacy/gateway.md`, `ports/semantic.md` |
**Imported by:** `service/ready_composition.md`

## Purpose

Provide the production `ExternalProviderFactory` that renders approved outbound cases through
`render_case` and builds `OpenAIResponsesEvaluator` instances for official OpenAI, Fireworks,
Vercel AI Gateway, and owner-declared OpenAI-compatible HTTPS endpoints — every profile that speaks
the Responses protocol. OpenAI-compatible Chat Completions hosts are a different protocol cell owned
by `adapters/providers/openai_chat_completions.md`; native Anthropic/Gemini protocols remain out of
scope entirely. Profile-ID dispatch across both cells is owned by `adapters/providers/factory.md`,
which calls this module rather than the other way round.

## Public surface

- `OpenAIResponsesExternalFactory`
- `external_factory_builders_from_config(config, clock) -> Mapping[ProviderBinding, Callable]`

## Behavior

Builders are empty when no provider binding is configured. Each builder key is the exact
`ProviderBinding` also required by privacy policy reconciliation. `render` returns exact body
bytes; `build_evaluator` returns a one-attempt Responses evaluator for the minted credential.

## Errors and edge cases

Unsupported transports, missing models, or non-HTTPS owner-declared origins produce no builder.
Factory/render failures surface through the gateway as unavailable/blocked outcomes.

## Invariants

1. No environment/config secret reads inside the factory.
2. Owner-declared path remains OpenAI Responses wire format only.
3. Every profile this module builds sends the Responses request shape; a Chat Completions profile
   reaching here is a dispatch-table defect, not a request this module adapts.

## Tests

Ready-composition and privacy gateway integration suites.

## Open questions

None.

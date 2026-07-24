# src/yoetz/adapters/providers/openai_responses_factory.py — OpenAI Responses ExternalProviderFactory

**Wave:** E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):**
`adapters/providers/openai_responses.md`, `adapters/privacy/gateway.md`, `ports/semantic.md` |
**Imported by:** `service/ready_composition.md`

## Purpose

Provide the production `ExternalProviderFactory` that renders approved outbound cases through
`render_case` and builds `OpenAIResponsesEvaluator` instances for official OpenAI, Fireworks, and
owner-declared OpenAI-compatible HTTPS endpoints. Native Anthropic/Gemini protocols are out of
scope.

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

## Tests

Ready-composition and privacy gateway integration suites.

## Open questions

None.

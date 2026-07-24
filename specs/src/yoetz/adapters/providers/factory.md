# src/yoetz/adapters/providers/factory.py — external provider factory dispatch table

**Wave:** E | **ADRs:** ADR-006, ADR-009, ADR-014 | **Imports (spec-tree):**
`adapters/providers/openai_responses_factory.md`,
`adapters/providers/openai_chat_completions.md`, `adapters/privacy/gateway.md`,
`config/models.md` | **Imported by:** `service/ready_composition.md`

## Purpose

Be the one place that answers "which runtime factory does this endpoint profile select?". A
profile the setup surface can write but no factory can build is not a neutral gap: the gateway
reports `factory_unavailable`, and the agent that asked for semantic review gets a check that
quietly never ran it.

## Public surface

- `external_factory_builders_from_config(provider, *, clock) -> dict[ProviderBinding, Callable]` —
  the single entry point ready composition installs.
- `CHAT_COMPLETIONS_ENDPOINT_PROFILES` / `ChatCompletionsEndpointFacts` — the closed table of
  Chat Completions profile IDs with their host, fixed path prefix, and recorded structured-output
  enforcement.
- `RESPONSES_ENDPOINT_PROFILE_IDS` — the profile IDs the Responses factory builds.
- `ChatCompletionsExternalFactory`, `chat_completions_profile_from_provider_config`, and
  re-exported `OpenAIResponsesExternalFactory` / `openai_profile_from_provider_config`.

## Behavior

A Responses-protocol profile is delegated to the Responses factory unchanged; a Chat Completions
profile builds a `ChatCompletionsExternalFactory`. Neither adapter learns the other's protocol.
Vercel's AI Gateway is a Responses surface on a different host, so it is a Responses profile with
no adapter of its own.

Chat Completions bindings carry an unknown data-use record — no reviewed record exists for those
endpoints — which keeps them out of the assisted-eligible path under ADR-006 decision 14. The
builder key is the exact `ProviderBinding` privacy-policy reconciliation also requires.

## Errors and edge cases

An absent provider or an unregistered profile ID yields no builder, which fails closed rather than
dispatching a wrongly shaped request to an unknown surface. Profile construction rejects a host,
port, path prefix, model, or timeout outside its recorded bounds.

## Invariants

1. Every endpoint profile ID the setup surface can write resolves here to exactly one factory.
2. Adding a profile ID without its endpoint facts is impossible: the facts are the table.
3. Dispatchable is not verified; live claims stay gated on E-007 evidence.

## Tests

`tests/unit/adapters/providers/test_factory_dispatch.py` owns profile-ID → factory selection,
exact host/port/path per preset, the unknown-profile empty result, and the unknown data-use facts.

## Open questions

None.

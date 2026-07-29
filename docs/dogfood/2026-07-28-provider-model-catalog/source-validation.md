# Independent model-source validation

Root-agent cross-check performed on 2026-07-28 after the Codex driver stopped.

Firecrawl was attempted first, as configured for this workspace, but all four provider-scoped
searches failed with HTTP 401. The fallback search was restricted to provider-owned documentation.
This file records conclusions and links, not an immutable copy of external pages.

## Confirmed identifiers

- OpenAI documents `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` as Responses API models:
  <https://developers.openai.com/api/docs/models>.
- Anthropic documents `claude-sonnet-5`, `claude-opus-4-8`, and
  `claude-haiku-4-5-20251001`:
  <https://platform.claude.com/docs/en/about-claude/models/overview>.
- Google documents `gemini-3.5-flash`, `gemini-3.6-flash`, and
  `gemini-3.5-flash-lite`:
  <https://ai.google.dev/gemini-api/docs/latest-model>.
- xAI documents `grok-4.5`, `grok-4.3`, `grok-4.20-0309-reasoning`, and
  `grok-4.20-0309-non-reasoning`:
  <https://docs.x.ai/developers/models>.
- Vercel documents the gateway slug `xai/grok-4.5`:
  <https://vercel.com/ai-gateway/models/grok-4.5>.

## Boundaries and residuals

- This validates published identifiers, not account entitlement, regional availability, or exact
  Yoetz structured-output interoperability.
- Anthropic's current catalog also includes newer `claude-opus-5` and `claude-fable-5`. Their
  omission is compatible with the implementation's documented non-exhaustive sample, but confirms
  that the static list is not a complete “all available models” catalog.
- OpenRouter and Vercel are aggregators with changing catalogs. Their lists remain samples and
  should eventually be refreshed or generated through an independently authorized discovery
  design if completeness becomes a requirement.
- The Fireworks `accounts/fireworks/models/minimax-m3` identifier has live provenance in this
  dogfood run. No live account call was made for the other suggestions.

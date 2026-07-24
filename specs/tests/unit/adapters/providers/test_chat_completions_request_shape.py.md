# tests/unit/adapters/providers/test_chat_completions_request_shape.py — Chat Completions adapter suite

**Wave:** E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):**
`src/yoetz/adapters/providers/openai_chat_completions.md`,
`src/yoetz/adapters/providers/openai_responses.md`, `src/yoetz/domain/privacy.md` |
**Imported by:** test runner

## Purpose

Freeze the two things about this adapter that are easy to get wrong and expensive to discover
live: the request shape an OpenAI-compatible Chat Completions host will actually accept, and the
refusal to turn a non-judgment answer into a passing review.

## Public surface

Assertions over `render_case`, `normalize_response`, `classify_provider_failure`, and
`ChatCompletionsProfile` validation.

## Behavior

Message content is asserted to be the approved payload as text, byte-identical to `case.payload`,
never a nested JSON value. `response_format` is present exactly when the profile records
`provider_enforced`, and the prompt-only body differs from the enforced body by that key alone.
Rendering is deterministic and digest-bound.

Response classification covers the exact judgment shape (success), prose from a host that ignored
the requested structure (invalid), refusal surface and `content_filter` (refused), `length`
truncation (timeout), empty content and an empty `choices` list (invalid), and the transport and
HTTP-status failure classes including `404` as `unsupported_profile`.

## Errors and edge cases

A profile with an unlisted base path prefix is rejected at construction. A non-external binding
cannot reach the adapter at all — the domain type refuses it first — and `render_case` still
rejects a wrong case or profile type.

## Invariants

1. A prose answer never becomes a clean semantic result.
2. No credential-shaped value appears in a profile, a rendered body, or a repr.

## Tests

This file is the executable owner. Whether a given host honors strict `response_format` is E-007
capability evidence, not a claim this suite can make.

## Open questions

None.

# tests/packaging/test_privacy_docs_and_resources.py — privacy artifact and publication boundary

**Wave:** F | **ADRs:** ADR-006, ADR-007, ADR-009 | **Imports (spec-tree):** root
PRIVACY/protocol docs, privacy schemas/mirrors, PRIV fixtures, public claim map | **Imported by:**
packaging suite and release workflow

## Purpose

Prove privacy promises, schemas and test corpus are complete, self-contained, byte-locked and free of
private strategy, paths, credentials, user content or unsupported claims in source/sdist/wheel.

## Public surface

Named tests verify root `PRIVACY.md`, two technical/setup docs, four root privacy schemas and four
installed byte-identical mirrors, eight test/sdist-only PRIV fixtures, schema/fixture manifests,
package resource manifest, wheel/sdist contents, links, public claims and canary scan.

## Behavior

Require the four schemas in root/schema/package manifests and exact source-installed byte parity.
Require eight fixtures in root fixture manifest and sdist test corpus but absent from installed wheel
resources. Validate every document statement against claim IDs/tests, every frozen enum/list against
domain authority, and every setup allowed/blocked example. Scan built artifacts for ignored
architecture inputs, host paths, canaries, secret fields, plaintext receipt fields, real endpoint/
credential material and wording that implies provider access or cross-channel consent. Reject copy
that claims full wire-byte commitment, allows a reusable SDK credential, or presents owner-only raw
traceback capture as a v0.1 mode.
Require public copy/claims/schema to state the initial-reservation no-receipt exception, terminal
receipt semantics, exact v0.1 commitment/audit-store fields, and the task-bundle versus taskless
privacy-audit storage boundary. Reject `dispatched`, `key_slot_ref`, or pending/approved state in a
finished receipt vocabulary.

## Errors and edge cases

Missing/extra/stale digest, duplicate `$id`, network `$ref`, generated widening, fixture packaged by
accident, broken link, unowned claim, schema drift, private provenance or canary match blocks release.
The scan operates on extracted artifacts without executing them or contacting a provider.

## Invariants

1. Four privacy schemas are installed byte-identically and resolve offline.
2. Eight privacy fixtures remain public test/sdist evidence, not runtime resources.
3. Public privacy copy maps to executable evidence and explicit limitations.
4. Built artifacts contain no private or secret material.
5. Root/docs/schema/manifests agree on exact vocabulary.

## Tests

Run `uv run --locked pytest tests/packaging/test_privacy_docs_and_resources.py -q` against both wheel
and sdist produced from a clean checkout.

## Open questions

None.

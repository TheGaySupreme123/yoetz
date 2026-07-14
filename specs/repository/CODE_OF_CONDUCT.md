# CODE_OF_CONDUCT.md — community behavior and escalation standard

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** none
**Imported by:** repository visitors, contributors, moderators, and release support

## Purpose

This file defines the behavioral standard for people interacting with the project. It is the public
community policy, separate from the technical security policy and separate from the contribution
workflow.

## Public surface

The document must include:

- acceptable and unacceptable behavior;
- a reporting path for enforcement issues;
- a statement that the standard applies in repository discussions and project spaces;
- a concise escalation or moderation process;
- the identity of the enforcement owner or team, if the project chooses to name one.

## Behavior

The code of conduct should set a clear baseline for respectful, constructive interaction. It should
describe how contributors and maintainers are expected to treat one another, and it should make the
moderation path obvious when behavior crosses the line.

The document must stay separate from technical specs:

- it must not describe runtime behavior;
- it must not cite private architecture notes;
- it must not become a hidden contribution contract;
- it must not promise moderation actions the project cannot actually enforce.

## Errors and edge cases

- A code of conduct that is too vague to report against is not useful.
- A code of conduct that is so punitive it chills ordinary project discussion is counterproductive.
- A behavior policy that tries to double as a legal license or security policy is the wrong document.

## Invariants

1. Community standards are public and easy to find.
2. The reporting path is separate from the security intake path.
3. Enforcement language stays bounded and understandable.
4. The file does not expose private operational details.

## Tests

- `tests/packaging/test_build_artifacts.py` — file presence in release artifacts.
- `tests/packaging/test_private_boundary_and_secret_scan.py` — no private content or secrets in the
  public policy text.

## Open questions

None.

F-002 and F-006 are the sole central community-policy gates.

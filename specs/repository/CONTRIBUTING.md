# CONTRIBUTING.md — contributor workflow and review expectations

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** `specs/README.md`,
`tests/unit.md`, `tests/conformance.md`, `tests/packaging.md`,
`.github/ISSUE_TEMPLATE/config.yml.md`, `.github/ISSUE_TEMPLATE/bug_report.yml.md`,
`.github/ISSUE_TEMPLATE/change_request.yml.md`, `.github/pull_request_template.md.md`,
`repository/AGENTS.md`
**Imported by:** new contributors, maintainers, release reviewers, and coding agents

## Purpose

This file tells contributors how to make changes that fit the project’s contract and how to get
those changes reviewed. It should reduce guesswork without becoming a second copy of the specs
tree. External contributions are welcome when they meet a high bar: issue-first intake, no
duplicates, clear verification, and disposition of every review comment.

## Public surface

The document must cover:

- contribution posture (open with rigor, not invitation-only and not “closed”);
- how to search for duplicates and open an issue before coding;
- which areas are design-gated and require maintainer acknowledgement on the issue;
- local setup expectations;
- how to find the authoritative specs;
- branch and patch submission expectations;
- the mandatory pull-request checklist and review-comment disposition rule;
- test commands or test families to run before asking for review;
- when to update specs instead of coding around ambiguity;
- how release-related and security-sensitive changes are reviewed;
- pointers to `SECURITY.md` and `CODE_OF_CONDUCT.md`.

## Behavior

The contributor guide should direct people toward the spec tree first. It should explain that the
implementation must follow the file-level specs and interface registry, and that changes to public
behavior need a spec update before or alongside code.

### Intake posture

Contributions are accepted when they follow the process below. The project is not invitation-only
and does not claim “not accepting contributions,” but unsolicited drive-by PRs without an issue,
duplicate search, or verification evidence are incomplete and may be closed or converted to an
issue.

### Before work

Contributors must:

1. Search existing issues and pull requests for duplicates before opening a new issue.
2. Open an issue using the repository issue forms (bug report or change request). A pull request
   without a linked issue is incomplete.
3. For design-gated areas, wait for maintainer acknowledgement on the issue before opening a PR.
   Design-gated areas are: protocol contracts, privacy and data-egress behavior, storage and
   durability, release pipelines and packaging surfaces, and changes that require a new ADR or a
   flip in `specs/OPEN_QUESTIONS.md`.
4. Find the owning spec for each touched file in `specs/FILE_MANIFEST.md`. If the change affects
   public behavior, update the owning spec before or alongside the code. Use names already
   registered in `specs/INTERFACES.md` for anything shared across modules.

### Local setup and making a change

This project uses `uv` for environment, lock, and build management, pinned per `pyproject.toml`.
Typical loop:

```text
uv sync
uv run pytest <path-to-touched-tests>
```

The pinned toolchain is Ruff for lint/format and the official npm-distributed Pyright
(`npx --no-install pyright`) in strict mode for type checking; Node/npm are contributor and CI
prerequisites only, never an end-user runtime requirement.

Contributors should:

- make the smallest change that satisfies the spec;
- run the relevant tests for the touched area (not the whole suite by default):
  - `tests/unit.md` for the touched module family;
  - `tests/conformance.md` when the change crosses a runtime/storage boundary;
  - `tests/packaging.md` when the change affects release artifacts or public metadata;
- avoid mixing unrelated behavioral changes in one patch;
- keep release and security-sensitive changes clearly labeled;
- preserve the public/private boundary and avoid adding private material under
  `docs/architecture/` or other ignored local drafting inputs to public files.

### Pull request and review

v0.1 uses a mandatory pull-request template. The PR must:

- link the issue (`Fixes #N` or `Refs #N`);
- confirm duplicate search and, when applicable, maintainer acknowledgement for design-gated work;
- list owning spec paths updated when behavior changes;
- paste the verification commands that were run;
- confirm the public/private boundary check;
- commit to disposition of review comments.

Every human reviewer comment and every code-review-agent comment must be dispositioned before
merge: either fix the finding, or reply explaining why it is invalid or out of scope. Silence,
deferral without agreement, or “will do later” without maintainer acceptance is not merge-ready.

Commands name only tools frozen by the release locks; contributor prose does not create a second
formatter, linter, type-checker, or review policy.

The contributor type checker is the exactly pinned official npm Pyright distribution invoked with
`npx --no-install pyright`; Node/npm are contributor and CI prerequisites only. T3 Code, OpenCode,
and Codex may inspire clarity of contributor experience and agent-facing docs, but their runtime
architectures and closed-contribution postures are not Yoetz design inputs. End users follow the
Python/`uv` install path; the delegation-only npm launcher at `support/npm-launcher/` (ADR-012)
is the one reviewed additional surface and stays unpublished until a separate release decision.

Agents and humans share the short contract in root `AGENTS.md`; `CONTRIBUTING.md` remains the
human process source of truth.

## Errors and edge cases

- A contribution that changes behavior without touching the authoritative spec is incomplete.
- A pull request without a linked prior issue is incomplete.
- A pull request that ignores open review-agent or maintainer comments without disposition is
  incomplete.
- A contribution guide that implies unreviewed shortcuts for release artifacts is misleading.
- The guide must not encourage editing generated or frozen artifacts by hand.
- Security vulnerabilities must not be filed as ordinary issues; conduct concerns must not use the
  security channel.

## Invariants

1. Specs govern behavior; the contribution guide only helps you use them.
2. Review expectations are clear before work starts.
3. Issue-first intake and duplicate search are required for external code changes.
4. Design-gated areas require maintainer acknowledgement on the issue before a PR opens.
5. Every review comment is fixed or answered before merge.
6. Release-critical changes get the same rigor as code changes.
7. Contributors are pointed to the right test families, not a vague “run everything” demand.

## Tests

- `tests/unit.md` — touched file families and behavioral expectations.
- `tests/conformance.md` — when a change crosses runtime/storage boundaries.
- `tests/packaging.md` — when the change affects release artifacts or public metadata.

## Open questions

None. F-005 is resolved; E-001 freezes the exact npm, Node, and Pyright versions.

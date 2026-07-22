# README.md — public project entry point and release overview

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-007, ADR-011 | **Imports (spec-tree):**
`specs/README.md`, `src/yoetz/__init__.md`, `src/yoetz/version.md`,
`tests/packaging.md`, `tests/subprocess.md`
**Imported by:** people visiting the repository root, release notes, package metadata, and support
links

## Purpose

This file is the first human-facing explanation of what `yoetz` is, how to install it, and
what it promises at v0.1. It is not the contract source of truth, but it must accurately summarize
the public surface without pointing readers to private planning docs.

## Public surface

The file must contain, at minimum, these sections:

- a one-paragraph project summary;
- a quick-start install and first-run path;
- a “what this project does” section that distinguishes the fail-safe strict-local installation
  seed from the user-visible assisted semantic-review recommendation;
- a “development status” section that clearly states the repo is a working draft or public-alpha
  style build, not a finished user guide;
- links to the repository security, contribution, changelog, and license pages;
- a short Contributing section that states contributions are welcome with a high bar (issue-first,
  no duplicates, design gates for sensitive areas) and points to `CONTRIBUTING.md` and `AGENTS.md`
  without claiming the project is closed or invitation-only;
- support routing: repository issues for ordinary bugs/questions, GitHub private vulnerability
  reporting or `security@yoetz.dev` for security, and `conduct@yoetz.dev` for private conduct reports;
- an explicit statement that the project is licensed under Apache-2.0;
- a short command summary for the console script and `python -m yoetz`;
- a clearly labeled optional `yoetz state capture --workspace PATH` support command that returns
  only bounded structural Git state digests, performs no ledger write, and is not a seventh MCP
  operation or a repository-content browser;
- a support boundary note that points readers to the specs tree for implementer detail.

## Behavior

The README explains the project in terms an installer or new contributor can act on. It may say how
to install from the published wheel or from a source checkout, but it must not invent hidden setup
steps, private URLs, or architecture notes that are not part of the public repository.

The README should describe the repository as:

- the public `yoetz` package and its release artifacts;
- a zero-egress deterministic installation seed plus optional user-configured semantic review;
- an inspectable recommended assisted-review balance for eligible no-training endpoint profiles:
  rich structured goal/timeline/deterministic-basis context, bounded linked recorded excerpts,
  sensitive content off, and direct reviewer challenges back to the main agent;
- a file-level spec tree under `specs/` for the implementation contract;
- a release that depends on reproducible packaging and locked dependencies.

The document may mention the six public workflow operations by name, but it must not re-specify
their behavior. It is allowed to point readers to `specs/README.md` and the relevant file-level
specs for that detail.

If the README advertises structural state capture, it says support is exact-cell capability gated,
local/read-only/content-withholding, and does not establish authorship, artifact verification, or
independent reproduction. It never describes the command as allowing the service or an MCP caller
to inspect an arbitrary workspace.

The README should keep the install story bounded:

- tell readers how to install the released package;
- explain that `yoetz` is the primary console entry point;
- mention `python -m yoetz` only as an equivalent invocation;
- avoid documenting unsupported developer-only paths as if they were public release paths.

The v0.1 document leads with the install/first-run story after its one-paragraph summary. It stays
text-only because the maintained Mermaid workflow lives in ADR-006. It states that routine checks,
retries, agent responses, and rechecks need no human after a standing policy is committed, while
setup/widening, credential changes, confirm-every-request, and finding waiver preserve explicit
human authority. The first-run story uses the human-facing brand names `Yoetz` and `Codex`, lists
automatically detected supported harnesses before any per-installation choice, and describes the
explicit no-default `Y`/`N` registration confirmation without changing lowercase command, MCP, or
wire identifiers. On macOS the install story may state that discovery includes both the shell PATH
and the standard Codex Desktop application location; on Windows it may name the reviewed Microsoft
Store package-family query. It must state that Linux has no official Codex App distribution rather
than implying a nonexistent app path, while preserving the same CLI selection flow.
It labels the three support routes clearly and never asks security or conduct reporters to use a
public issue. E-012 verifies the private routes before release.

## Errors and edge cases

- The README must not claim the repository is production-complete unless the release process says
  so.
- It must not reference ignored private docs as required reading.
- It must not promise runtime capabilities that are only available in a selected profile or via an
  optional extra without saying so.
- It must not expose secrets, local paths, or repository split history.

## Invariants

1. The README is honest but non-normative.
2. The README never outranks the spec tree or the packaged metadata.
3. The README explains, it does not implement.
4. The README can be updated for phrasing, but it must not drift from the frozen release contract.

## Tests

- `tests/packaging/test_build_artifacts.py` — README presence and metadata inclusion.
- `tests/packaging/test_wheel_and_sdist_contents.py` — README byte parity in source and wheel
  metadata.
- `tests/conformance/surfaces/test_cli_contract_matrix.py` — public command help links back to the
  same project story.

## Open questions

None.

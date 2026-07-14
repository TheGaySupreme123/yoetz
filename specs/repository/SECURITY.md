# SECURITY.md — vulnerability reporting and disclosure policy

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** `tests/packaging.md`,
`tests/subprocess.md`
**Imported by:** the repository homepage, release artifacts, and security reporters

## Purpose

This file tells external reporters how to send security issues to the project and what response
shape to expect. It protects users by keeping exploitation details out of ordinary issue threads
and by making the disclosure path explicit.

## Public surface

The file must describe:

- the preferred vulnerability reporting channel;
- the supported security-response window;
- what information the reporter should include;
- what the project will and will not do in public before a fix is ready;
- how to handle urgent disclosure when the issue is actively exploitable.

## Behavior

The security policy must tell reporters how to share a problem safely and privately. It should
encourage minimal reproduction details, version information, and impact description, but it must
not ask for secrets, live customer data, or destructive proof in public.

The document should also state:

- whether the project accepts GitHub security advisories, private email, or both;
- whether there is an expected acknowledgment time;
- whether reporters may coordinate a fix window before public disclosure;
- which release lines are considered supported for security reporting;
- how the project handles third-party vulnerability disclosures that affect its dependencies.

The policy must avoid promising instant triage or unconditional response times unless the project
actually commits to them.

During public alpha, the newest published release is the only security-fix line promised; reports
against older versions are still accepted and triaged, but backports are not promised. The actual
private intake route is inserted only after F-006 supplies a maintained channel.

## Errors and edge cases

- A security contact that routes to a public issue tracker without warning is not acceptable for the
  release boundary.
- The policy must not require reporters to expose secrets to prove a vulnerability.
- The policy must not disclose internal infrastructure details that are unnecessary for reporting.

## Invariants

1. Security reporting is private by default.
2. The policy is short, clear, and actionable.
3. The policy does not weaken the public release boundary.
4. The policy can name supported versions without exposing hidden internals.

## Tests

- `tests/packaging/test_private_boundary_and_secret_scan.py` — no secrets or private paths in the
  public security document.
- `tests/packaging/test_build_artifacts.py` — security policy included in the release artifact.

## Open questions

None.

F-006 is the sole central public-contact gate.

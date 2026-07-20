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
- how to handle urgent disclosure when the issue is actively exploitable;
- a short threat-model / out-of-scope table so reporters know what is in policy scope.

## Behavior

The preferred route is GitHub private vulnerability reporting for this repository, with
`security@yoetz.dev` as a distinct maintained fallback. The security policy must tell reporters how
to share a problem safely and privately. It should encourage minimal reproduction details, version
information, and impact description, but it must not ask for secrets, live customer data, or
destructive proof in public.

The document should also state:

- whether the project accepts GitHub security advisories, private email, or both;
- whether there is an expected acknowledgment time;
- whether reporters may coordinate a fix window before public disclosure;
- which release lines are considered supported for security reporting;
- how the project handles third-party vulnerability disclosures that affect its dependencies.

The policy must avoid promising instant triage or unconditional response times unless the project
actually commits to them.

During public alpha, the newest published release is the only security-fix line promised; reports
against older versions are still accepted and triaged, but backports are not promised. E-012 must
prove private reporting is enabled and the fallback mailbox is monitored before public release.

The policy must include a concise threat-model / out-of-scope table covering at least:

| Topic | In scope for this policy | Out of scope / reporter responsibility |
| --- | --- | --- |
| Yoetz package, official docs, release artifacts | Yes | Unrelated third-party tools you run alongside Yoetz |
| Local service, storage, privacy/egress gateway | Yes — defects in Yoetz enforcement | Operator misconfiguration that widens policy after informed consent |
| Provider / MCP destinations you enable | Defects in Yoetz classification, binding, or audit | Trustworthiness of an external provider or MCP server you chose |
| Agent / harness integrations | Defects in Yoetz-owned bridges and guidance delivery | Host agent bugs outside Yoetz adapters |
| Social engineering, physical access, compromised host OS | Only when Yoetz fails a stated boundary | General host compromise outside Yoetz’s control |

The table must stay short and must not invent sandbox or remote-hardening claims Yoetz does not
make. It must not ask reporters to paste secrets to prove a finding.

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

None. F-006 is resolved; E-012 is the operational contact-route gate.

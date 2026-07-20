# Security policy

## Reporting a vulnerability

Please use [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository. If that is not available to you, email **security@yoetz.dev** as a maintained
fallback.

Please include:

- affected version (`yoetz version --json` output if you have it);
- a minimal reproduction;
- the impact you believe the issue has;
- any suggested mitigation, if you have one.

Please do **not** include secrets, live customer data, or a destructive proof-of-concept in a public
issue, a private report attachment you did not need to create, or anywhere outside the private
channel above.

## What to expect

- We accept both GitHub private security advisories and email reports to the fallback address.
- We aim to acknowledge a new report, but during public alpha we do not commit to a fixed response
  time — please do not assume an unconditional SLA.
- We are willing to coordinate a private fix window with a reporter before public disclosure.
- During public alpha, **the newest published release is the only line we promise a security fix
  for.** Reports against older versions are still accepted and triaged, but a backport is not
  promised.
- Third-party vulnerabilities affecting a Yoetz dependency are handled the same way: report
  privately, and we will coordinate an upgrade and, where warranted, our own advisory.

## Scope

This policy covers the Yoetz package, its official documentation, and its release artifacts in this
repository. It does not cover unrelated third-party integrations you have configured yourself.

### Threat model / out of scope

| Topic | In scope for this policy | Out of scope / reporter responsibility |
| --- | --- | --- |
| Yoetz package, official docs, release artifacts | Yes | Unrelated third-party tools you run alongside Yoetz |
| Local service, storage, privacy/egress gateway | Yes — defects in Yoetz enforcement | Operator misconfiguration that widens policy after informed consent |
| Provider / MCP destinations you enable | Defects in Yoetz classification, binding, or audit | Trustworthiness of an external provider or MCP server you chose |
| Agent / harness integrations | Defects in Yoetz-owned bridges and guidance delivery | Host agent bugs outside Yoetz adapters |
| Social engineering, physical access, compromised host OS | Only when Yoetz fails a stated boundary | General host compromise outside Yoetz’s control |

Yoetz does not claim a general-purpose sandbox for untrusted code. Reports that only restate
documented operator choices (for example, enabling an egress destination) are usually out of scope
unless Yoetz failed classification, binding, minimization, or audit.

## Public disclosure

We will not discuss unpatched vulnerability details in public issues, discussions, or chat before a
fix is available. Once a fix ships, we credit reporters who want credit and publish what is
necessary for users to assess their exposure.

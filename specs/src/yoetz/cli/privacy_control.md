# src/yoetz/cli/privacy_control.py — trusted foreground privacy preview/decision helper

**Wave:** D | **ADRs:** ADR-008, ADR-009 | **Imports (spec-tree):**
`service/confidential_client.md`, `service/confidential_protocol.md`, `cli/unlock.md` |
**Imported by:** `cli/app.md` privacy decision commands

## Purpose

Renders exact pending policy/disclosure previews on the controlling terminal and collects one
explicit human decision. Durable policy widening uses strong OS/established-passphrase
reauthentication; confirm-every-request disclosure already within policy uses digest-bound TTY
consent without reauthentication. Neither is exposed to stdout, MCP, agents, or reusable tokens.

## Public surface

- `decide_policy(pending_id)` and `decide_disclosure(pending_id)`; IDs select state but grant no
  authority.
- Private terminal-only preview/choice helpers. No `--approve`, `--yes`, decision JSON, stdin,
  environment, config, file, or noninteractive API.

## Behavior

Require foreground controlling `/dev/tty`, connect to the separately authenticated human-control
YZH1 endpoint with `HumanControlClient`, open one exact pending-ID ceremony, receive/freeze the
service-minted bounded preview/binding, render only on TTY, and collect approve/deny/edit. Policy-
widening approval follows the server-selected strong OS-presence or established-passphrase phase.
Confirm-every-request disclosure must say `authorization_change=none`; its exact TTY approve/deny
goes directly to the one-case audit decision with no reauthentication. The service commits
internally and returns only structural outcome; edit exits to create a new proposal.

## Errors and edge cases

No TTY/background/redirection/pipeline/timeout/signal/stale digest fails closed with terminal
restoration. Preview/decision never enter stdout/JSON/logs/history. Same-UID automation limit is
documented.

## Invariants

1. Normal CLI boolean/argument cannot approve widening/disclosure.
2. Preview/reauth/decision bind to one exact digest/generation/session.
3. Helper receives no reusable proof/token.
4. The pending ID is not a binding; only the service-created YZH1 ceremony can mint one.
5. The helper imports no server-side human-control, ingress, unlock, vault, application, or provider
   module.
6. Per-request consent cannot create policy authority; policy widening cannot use mere TTY consent.

## Tests

- `tests/subprocess/test_privacy_human_control.py` covers PTY-only behavior/forbidden channels.
- `tests/integration/service/test_human_control.py` covers service binding/commit.

## Open questions

None.

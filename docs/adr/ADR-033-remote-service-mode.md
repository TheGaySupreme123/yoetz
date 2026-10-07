# ADR-033 — Optional remote service mode

**Status:** Proposed for issue #903. The maintainer requested this design record on 2026-10-01
and requested the local contract the same day. Remote forwarding stays deferred in
[`docs/OPEN_QUESTIONS.md`](../OPEN_QUESTIONS.md) until the founder questions below are accepted
in that ledger.
**Implemented by:** `src/yoetz/application/remote_mode.py`, `yoetz remote` in
`src/yoetz/cli/app.py`, `/remote` in `src/yoetz/tui/app.py`, and the local recovery tokens in
`src/yoetz/protocol/recovery.py`.
**Relates to:** ADR-001, ADR-002, ADR-008, ADR-009, ADR-011, ADR-018, and issue #903.

## Context

ADR-001 makes one persistent local service the only owner of the installation catalog, task-bundle
writers, vault keys, and provider gateway. CLI, MCP, hooks, and the terminal interface are clients
of that service. ADR-001 also defers distributed service access and TCP/network control. Issue #903
asks for an optional mode in which a local install forwards workflow to a remote Yoetz server over
HTTPS or SSH, so the ledger, checks, AI-powered review, and receipt composition run there.

That request crosses the local trust boundary and the egress boundary. This record fixes the parts
the existing authorities already determine, and lists the parts that remain a maintainer decision.
The local record and its commands exist. No wire schema, fixture, or remote adapter is added.

## Decisions

1. **Local mode stays the default.** An installation with no remote connection keeps today's
   singleton local service, Unix-domain control channel, vault, ledger, and provider gateway.
   Remote mode is opt-in. It does not replace local mode, sync a local ledger with a remote one,
   or introduce a second writer for the same bundle.

2. **Agent-facing processes stay clients.** Hooks, the MCP bridge, the CLI, and the TUI keep
   talking to the trusted local service over the authenticated local control channel (ADR-001,
   ADR-008). They do not open the remote connection or hold the remote credential. The owner
   records an endpoint with `yoetz remote` or `/remote`; those commands write a local file and
   do not ask the service to forward. The local service is the only process that may forward a
   workflow request, and this slice does not give it that ability.

3. **What stays on the machine.** Host registration, hook and MCP intake, the CLI, the TUI, and
   structural subject-state capture (ADR-011) stay local. Capture remains a client-local support
   capability: it reads the worktree here and still emits no source bytes.

4. **What the remote server owns once a connection is established.** The remote server owns the
   catalog and task-bundle writers, projections, local checks, AI-powered review and its provider
   gateway, and receipt composition for that connection. The local service does not open those
   stores and does not dispatch a provider call for work it forwarded. It may keep a local vault
   whose only remote-mode purpose is holding the remote credential (decision 6).

5. **One operation contract, two remote transports.** `start`, `publish_work`, `check`,
   `respond`, `receipt`, and `status` keep the public request and response shapes in
   [`schemas/`](../../schemas/) (ADR-002). HTTPS and SSH carry those shapes. They do not change
   them. The local Unix-domain control protocol, including its method allowlist and same-UID peer
   check, remains the path from a local client to the local service. `SO_PEERCRED` /
   `getpeereid` do not authenticate a remote peer.

6. **Credential handling follows ADR-008.** An API key, a future OAuth token, and any SSH
   private key Yoetz stores are secret material. They enter through the existing confidential
   secret path, live in the local vault, and never appear in config, argv, environment, logs,
   hook output, or MCP frames. Plain HTTP is refused. HTTPS requires TLS. SSH uses a client key
   the server has authorized and a server host key the client has verified. The credential type
   is pluggable so a later OAuth credential can replace the API key for that remote. The two are
   not used together. The local slice records the label `api_key` and does not accept, store, or
   send the secret, so the vault secret path is not wired until enrollment is accepted.

7. **The route ceiling does not move.** ADR-018's strict MCP route still refuses to request
   AI-powered review. Forwarding a check to a remote server does not bypass that ceiling.

8. **Reverse operations exist locally and do not forward.** `yoetz remote configure`, `status`,
   `disconnect`, and `connect`, and the terminal command `/remote`, record or clear an endpoint
   on this machine. `connect` fails closed with `remote_egress_not_authorized` and opens no
   socket. Disconnect returns the installation to local mode under decision 1. An HTTPS endpoint
   is stored as `https://host[:port]` with no user, password, path, query, or fragment. An SSH
   target is `[user@]host[:port]`. The on-disk document uses the schema string
   `yoetz.remote-mode/1`. That string is not a wire contract under `schemas/`.

9. **Receipts will name the producing service.** A later schema slice records whether the local
   service or a remote server produced the receipt, and which transport was used. No receipt
   field is added in this slice, because the producing service is always the local one.

10. **Out of scope for this decision.** Multi-tenant hosting, a hosted Yoetz service, ledger
    sync, making remote mode the default, concurrent independent writers, and a client that
    opens storage itself.

## Founder questions

These stay open. Silence here is not an answer, and no adapter may send ledger content or
captured repository state off the machine until they are accepted in `docs/OPEN_QUESTIONS.md`.

| Question | Why it is still open |
|---|---|
| Egress channel | ADR-009's `llm_inference` channel is for provider review. Its non-LLM channels cannot carry task or user content. Remote mode would send ledger content and captured state off the machine, so it needs its own accepted channel decision. |
| Vault keys | Whether the remote server holds the only keys, or the client keeps a local key and sends ciphertext. |
| Connection loss | Whether an unreachable server fails closed, falls back to the local service, or queues for replay. Hook deadlines (including the drain preflight and the MCP time limit in #886) are part of this choice. |
| Enrollment | How an API key or SSH key is enrolled, rotated, and revoked on the server. |
| OAuth | Which future flow applies, and what happens to a stored API key when the owner signs in or out. |
| Who runs the server | Whether the remote server is one the owner hosts, a Yoetz-operated service, or either. |

## Consequences

- ADR-001's deferral of distributed service access and TCP/network control stands for the
  shipping product. This record is the proposed shape of the exception, and it does not lift
  the deferral.
- Local clients gain no new trust from the local record. A hook or MCP process that opened its
  own remote socket would violate ADR-008.
- Status always reports `service: local` and `forwarding: false`. `yoetz remote connect` performs
  no network I/O.
- A transport adapter still waits on an accepted egress channel and the other founder questions,
  then a schema and fixtures for the remote connection contract. Host runbooks record that Codex,
  Claude Code, and Cursor stay on the local service.

## Alternatives considered

| Approach | Result | Reason |
|---|---|---|
| Design record now, transport after the founder questions | **Selected** | The trust and egress choices change the protocol. Coding a client first would freeze them by accident. |
| Local record now, `connect` fails closed | **Selected** for the local slice | Configure, status, and disconnect are reversible and send nothing. Connect does not choose an egress channel. |
| MCP and hooks dial the remote server directly | Rejected | Those processes are untrusted clients. Putting the credential there breaks ADR-008. |
| Reuse `update_checks` or `llm_inference` as the remote channel | Rejected | Those channels have different payloads and consent. Remote ledger traffic is not a version check and not a provider call. |
| Replace the local service | Rejected | ADR-001's local singleton is the default, and #903 keeps local mode when no remote is connected. |

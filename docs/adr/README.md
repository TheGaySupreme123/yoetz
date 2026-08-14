# Architecture decision records

ADRs are the top authority for public behavior. When an ADR and a page under `docs/` disagree, the
ADR wins; when an ADR and the code disagree, that is a bug in one of them and worth an issue.

| ADR | Decision |
|---|---|
| [001](ADR-001-writer-topology.md) | Writer topology |
| [002](ADR-002-canonical-protocol.md) | Canonical protocol |
| [003](ADR-003-storage-sqlite-durability.md) | Storage: SQLite durability |
| [004](ADR-004-threat-crypto-key-recovery.md) | Threat model, crypto, key recovery |
| [005](ADR-005-codex-capability-identity.md) | Codex capability identity |
| [006](ADR-006-semantic-provider-profile.md) | Semantic provider profile |
| [007](ADR-007-packaging-platform-release.md) | Packaging, platform, release |
| [008](ADR-008-local-service-vault-trust-boundary.md) | Local service and vault trust boundary |
| [009](ADR-009-data-egress-privacy.md) | Data egress and privacy |
| [010](ADR-010-harness-integration-port.md) | Harness integration port |
| [011](ADR-011-structural-subject-state-capture.md) | Structural subject-state capture |
| [012](ADR-012-first-run-setup-wizard.md) | First-run setup wizard |
| [013](ADR-013-interactive-control-menu.md) | Interactive control menu |
| [014](ADR-014-toml-settings-and-owner-declared-endpoint.md) | TOML settings and owner-declared endpoint |
| [015](ADR-015-elevated-bootstrap-consent.md) | Elevated bootstrap consent |
| [016](ADR-016-human-review-non-default-actions.md) | Human review for non-default actions |
| [017](ADR-017-full-screen-terminal-interface.md) | Full-screen terminal interface |
| [018](ADR-018-host-declared-mcp-route-egress-ceiling.md) | Host-declared MCP route egress ceiling |
| [019](ADR-019-declared-completion-scope.md) | Declared completion scope |
| [020](ADR-020-typed-evidence-digest-provenance.md) | Typed evidence digest provenance |
| [021](ADR-021-recommended-defaults-advisories-and-update-check-surfacing.md) | Recommended-defaults advisories and update-check surfacing |
| [022](ADR-022-harness-observation-writer-identity-and-observation-tolerant-concurrency.md) | Harness observation writer identity and observation-tolerant concurrency |

Unresolved gates are centralized in [`docs/OPEN_QUESTIONS.md`](../OPEN_QUESTIONS.md), not scattered
through individual ADRs.

## A note on the retired spec tree

Yoetz was built spec-first: every planned file had one Markdown owner at a mirrored path under
`specs/`. That tree completed its purpose — all 626 declared files were built — and was retired on
2026-07-25 rather than maintained as a second copy of a shipped system.

Each ADR's **Implemented by** line now names real modules, suites, and resources. Some ADR bodies
still contain prose written while the tree existed; where such text refers to an "owning spec", read
it as the code and tests for that path. The final state of the tree is recoverable in full:

```text
git show specs-tree-final:specs/FILE_MANIFEST.md
git show specs-tree-final:specs/<path>
```

# Second dogfood remediation residuals

**Date:** 2026-07-22  
**Remediation branch:** `cursor/yoetz-dogfood-ci-remediation-9715`  
**Analysis:**
[`2026-07-22-codex-testing-yoetz-second-dogfood-analysis.md`](2026-07-22-codex-testing-yoetz-second-dogfood-analysis.md)  
**Prior activation report:**
[`2026-07-22-codex-testing-yoetz-activation.md`](2026-07-22-codex-testing-yoetz-activation.md)

This note records what the remediation branch did and did not close relative to the second dogfood
analysis. It is not a productization plan and does not absorb experimental multiprovider work.

## Residual limitations after remediation

### Codex dogfood multiprovider experiment not productized

The multiprovider experiment on `dogfood/codex-testing-multiprovider` remains experimental-branch
evidence only. Analysis defects **4**, **5**, and **7** (SDK canonical-body / wire mismatch,
provider factories registered while the ready application keeps `_semantic_not_configured`, and
discard of the explicit `request_commitment` at the factory boundary) stay on that branch. This
remediation branch and `main` do **not** absorb or ship that generated provider work.

### Defect 8: reinstall / `uvx` exact candidate before runtime claims (process)

Installed-artifact lag (source-tree tests green while `$HOME/.local/bin/yoetz` or the active `uvx`
candidate stays behind) is a dogfood-process residual. Before any future dogfood runtime claim,
reinstall or invoke the exact candidate under test. Remediation does not change that process gate.

### No observation channel yet implemented (now in-scope for v0.1)

At the second dogfood, Yoetz still recorded only what participants published — there was no
independent workspace or harness observation channel, so cooperative MCP remained the only tracking
path. **Contract amendment (2026-07-22):** first-party Codex observation is now a required v0.1
capability (protocol stays `0.1`) via local `ObservationPort` control, not a seventh MCP tool
(ADR-010). Implementation and capability evidence remain outstanding; until they land, runtime
behavior stays cooperative-only.

### Auto-activation unproven without Yoetz-specific wording

The second run's successful `start` followed an explicit user instruction to use Yoetz. That does
not prove spontaneous activation on a normal material request without Yoetz-specific wording.
Activation evidence remains conditioned on that prompt shape.

### Skill unsupported until E-002 evidence (empty profiles remain)

Packaged skill install still rejects an empty `harness_tested_set`. Manifest fields such as
`capability_profile_ids` and related harness test/support sets remain empty until E-002 evidence
exists. This remediation does **not** populate those fields; skill support stays unavailable on the
supported path.

### Compaction only re-grounds published state

Compaction recovery can only re-ground from durable published work plus a usable `status` read.
With no accepted publication, persistence alone does not restore task continuity. That boundary is
unchanged.

### Packaging offline `uv pip install --offline` may soft-fail without primed cache

Clean/offline packaging verification can soft-fail when the uv cache is not primed (for example
`uv pip install --offline` against an empty or restricted `$HOME/.cache/uv`). That CI soft-fail
residual remains; it is separate from MCP usability and cooperative-publication defects.

### What was fixed on this remediation branch

Relative to defects and CI friction exposed by the second dogfood analysis and follow-on
remediation:

- **`EVENT_INVALID` surfacing** — known publication validation failures (including canonical
  set-ordering / unsorted set fields) project as actionable public errors instead of opaque
  `INTERNAL_ERROR`.
- **`client.id` location UX** — forbidden or misplaced `client.id` (and related client-shape)
  validation failures surface with clearer location feedback so agents can stop inventing wrapper
  fields while reconstructing requests.
- **MCP presentation schemas** — flattened presentation input schemas are advertised so tool
  declarations are usable beyond collapsed `unknown` compositions for the targeted Codex surface.
- **Setup honesty** — provider/setup readiness wording is layered so “configured/stored” is not
  collapsed into an overstated “ready to use” claim.
- **Guidance / ADR-012 align** — tier-zero guidance and ADR-012 argv / `--api-key` compatibility
  wording are aligned so agents are not forced to resolve a false conflict by reading the ADR alone.
- **CI format / PRIV-PATH / CLI assert** — format gates, PRIV-PATH public-boundary scanning, and
  related CLI/provider-flag asserts are unblocked on this branch.

These fixes address agent usability, honesty, and CI hygiene from the second run. They do not
productize the multiprovider experiment, invent observation, enable the skill without E-002, or
prove auto-activation without Yoetz-specific wording.

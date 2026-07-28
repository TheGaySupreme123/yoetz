Implemented the provider model-selection experience across all seven reviewed presets.

Key outcomes:

- Shared deterministic, repository-owned model picker.
- Maximum 10 suggestions, with the existing default first.
- Explicit `Custom model ID` option everywhere applicable.
- Interactive endpoint, explicit-provider, and secure setup paths are consistent.
- Scripted `--model` values remain unchanged and bypass the picker.
- No new network discovery channel or credential exposure.
- Owner-declared endpoints remain manual.

Verification passed:

- Focused: `93 passed`
- Expanded CLI/config: `127 passed`
- Boundary/packaging: `59 passed, 4 xfailed`
- Ruff: passed
- Pyright: `0 errors`
- `git diff --check`: passed

The detailed report is at [codex-final-report.md](/Users/shayb/yoetz-core/docs/dogfood/2026-07-28-provider-model-catalog/codex-final-report.md).

Yoetz closure:

- Final semantic-required check succeeded with no findings.
- Final receipt: `rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a`
- Receipt digest: `sha256:0668927dc52e30d629617801907279b2de1757fe140cb3f9f77057e8da1c7465`
- Receipt replay returned the same durable identifiers.
- Final frontier: sequence `37`
- Open obligations/findings: `0 / 0`

Yoetz coverage remains self-asserted, published-only, and metadata-only; it is not independent proof of correctness. Live account availability and interoperability of suggested models were not tested.

No commit, issue, PR, push, merge, or external publication was created.
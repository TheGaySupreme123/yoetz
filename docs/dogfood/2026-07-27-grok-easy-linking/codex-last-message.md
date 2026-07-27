Implemented and verified Grok/xAI easy linking on the requested branch.

- Added `--grok`, `--provider grok`, and aliases `xai`/`x-ai`.
- Added exact `api.x.ai/v1` Chat Completions factory routing and tests.
- Preserved credential, privacy, egress, provenance, and receipt boundaries.
- xAI endpoint and structured-output compatibility are supported by [official xAI docs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs), but live Grok dispatch remains unverified.
- Verification: 72 tests passed, Ruff passed, Pyright clean, public-boundary scan passed.
- Yoetz task: `tsk_861ccfd3-2781-4d92-91c9-96e4215b28cb`
- Receipt: `rcp_4b302cb2-a9f1-41a2-99fb-aa74b60372c6`
- No commit, push, issue, or PR created.

Detailed report: [codex-final-report.md](/Users/shayb/yoetz-core/docs/dogfood/2026-07-27-grok-easy-linking/codex-final-report.md:1)
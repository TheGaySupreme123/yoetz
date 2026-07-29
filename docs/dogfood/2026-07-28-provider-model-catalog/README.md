# 2026-07-28 provider model catalog dogfood

This directory preserves public, privacy-safe materializations of the Codex assignment and event
evidence, independent observer reports, Yoetz semantic/runtime evidence, and the final
implementation report. Host-specific absolute paths are normalized to `<repository-root>` or a
tool name in the public artifacts.

| Artifact | Role |
| --- | --- |
| `codex-prompt.md` | Public assignment given to `codex-testing`, with its workspace path normalized |
| `codex-events.jsonl` | Materialized public JSONL event stream with host paths normalized |
| `codex-last-message.md` | Final response emitted by `codex-testing` |
| `codex-final-report.md` | Agent-authored implementation and Yoetz report |
| `agent-quality-live.md` | Observer 1 chronological practical-quality notes |
| `agent-quality-audit.md` | Observer 1 final assessment |
| `yoetz-health-live.md` | Observer 2 chronological Yoetz-health notes |
| `yoetz-health-audit.md` | Observer 2 final assessment |
| `source-validation.md` | Root-agent provider-owned source cross-check |
| `run-meta.md` | Branch, baseline, launcher, model, and run identity |
| `synthesis.md` | Root-agent cross-check and final synthesis |

The two observer lanes are intentionally independent. Registration or tool discovery alone is not
Yoetz activation, and semantic success requires validated semantic output plus provenance and
receipt/recovery evidence.

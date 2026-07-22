# tests/unit/adapters/test_codex_session_stream.py

Covers incremental JSONL cursor behaviors: partial lines, truncation, restart, and hook/stream
dedup via the local observation store.

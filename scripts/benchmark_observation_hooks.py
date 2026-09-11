"""Concurrent public-safe native-adapter probe; no service, vault or host login.

This measures adapter execution, not a vendor-host deadline or RPC acceptance.
All children use an explicit fresh local store. Never pass a live state root.
"""

from __future__ import annotations

import argparse
import io
import json
import multiprocessing
import tempfile
import time
from pathlib import Path
from typing import Any


def _invoke(root: str, host: str, lane: int) -> dict[str, Any]:
    from yoetz.cli.observe_hooks import handle_claude_observe, handle_cursor_observe, handle_observe
    from yoetz.domain.observation_profiles import (
        CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
    )

    state = Path(root) / "state"
    workspace = str(Path(root) / "workspace")
    payload = {
        "session_id": f"probe-{lane}",
        "tool_name": "Bash",
        "tool_use_id": f"call-{lane}",
        "tool_input": {"command": "true"},
        "event_ordinal": 1,
    }
    arguments: dict[str, Any] = {
        "event_name": "PreToolUse",
        "stdout": io.BytesIO(),
        "workspace": workspace,
        "_state": state,
        "skip_service": True,
    }
    if host == "claude":
        handler = handle_claude_observe
        arguments["observation_profile"] = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    elif host == "cursor":
        handler = handle_cursor_observe
        arguments["event_name"] = "preToolUse"
        arguments["observation_profile"] = CURSOR_ORDINARY_OBSERVATION_PROFILE_ID
        payload["conversation_id"] = payload.pop("session_id")
    else:
        handler = handle_observe
        payload["tool_name"] = "shell"
        payload["correlation_id"] = payload["tool_use_id"]
    arguments["stdin_bytes"] = json.dumps(payload).encode()
    started = time.monotonic_ns()
    code = handler(**arguments)
    ended = time.monotonic_ns()
    return {
        "lane": lane,
        "start_ns": started,
        "end_ns": ended,
        "ms": (ended - started) / 1e6,
        "exit_code": code,
    }


def _worker(root: str, host: str, lane: int, barrier: Any, results: Any) -> None:
    # Import before the barrier; the reported boundary excludes interpreter
    # startup and explicitly measures concurrent adapter/store execution.
    import yoetz.cli.observe_hooks  # noqa: F401

    barrier.wait(timeout=30)
    results.put(_invoke(root, host, lane))


def _seed(root: Path, retained: bool) -> tuple[Any, str]:
    from yoetz.adapters.integrations.observation_local import (
        LocalObservationStore,
        ObservationOutboxRow,
    )
    from yoetz.cli.observe_hooks import map_hook_payload_to_envelope

    store = LocalObservationStore(_state=root / "state")
    workspace = store.workspace_commitment(str(root / "workspace"))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "seed")
    if retained:
        with store.batched(workspace):
            state = store._load(workspace)  # pyright: ignore[reportPrivateUsage]
            assert state.envelopes is not None and state.quarantine is not None
            assert state.pending_outbox is not None
            now = store._wall_timestamp()  # pyright: ignore[reportPrivateUsage]
            for ordinal in range(509):
                envelope = map_hook_payload_to_envelope(
                    "PostToolUse",
                    {
                        "session_id": "seed",
                        "tool_name": "shell",
                        "exit_status": 1,
                        "correlation_id": f"seed-{ordinal}",
                    },
                    session_commitment=session,
                    event_ordinal=ordinal + 1,
                    key_material=store.key_material(),
                )
                if ordinal < 250:
                    state.envelopes.append(envelope)
                elif ordinal < 310:
                    state.pending_outbox.append(ObservationOutboxRow("seed", envelope))
                else:
                    state.quarantine.append(("seed", envelope, "service_unavailable", now))
            store._save(workspace, state)  # pyright: ignore[reportPrivateUsage]
    return store, workspace


def run(host: str, fanout: int, retained: bool) -> dict[str, Any]:
    # Resolve macOS's temporary path before creating an owner-only directory.
    with tempfile.TemporaryDirectory(
        prefix="yz-hooks-", dir=Path(tempfile.gettempdir()).resolve()
    ) as name:
        root = Path(name)
        root.chmod(0o700)
        (root / "workspace").mkdir()
        store, workspace = _seed(root, retained)
        before = store.selection_accounting(workspace)
        before_pending = store.pending_outbox_count(workspace)
        before_quarantine = store.quarantined_count(workspace)
        before_bytes = store._workspace_path(workspace).stat().st_size
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(fanout + 1)
        results = context.Queue()
        workers = [
            context.Process(target=_worker, args=(name, host, lane, barrier, results))
            for lane in range(fanout)
        ]
        try:
            for worker in workers:
                worker.start()
            barrier.wait(timeout=30)
            samples = [results.get(timeout=30) for _ in workers]
            for worker in workers:
                worker.join(timeout=10)
                if worker.exitcode != 0:
                    raise RuntimeError("probe_worker_failed")
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=10)
            results.close()
        from yoetz.cli.hook_diagnostics import hook_diagnostic_summary
        from yoetz.protocol.canonical import canonical_encode

        after = store.selection_accounting(workspace)
        lanes = {row.codex_session_id for row in store.list_pending_outbox_rows(workspace)}
        prefix = {"codex": "", "claude": "claude:", "cursor": "cursor:"}[host]
        for sample in samples:
            sample["retained"] = f"{prefix}probe-{sample['lane']}" in lanes
        observed = after["observed_count"] - before["observed_count"]
        values = sorted(sample["ms"] for sample in samples)

        def percentile(p: int) -> float:
            return values[(len(values) * p + 99) // 100 - 1]

        return {
            "host": host,
            "fanout": fanout,
            "retained": retained,
            "diagnostics": json.loads(
                canonical_encode(hook_diagnostic_summary(_state=root / "state"))
            ),
            "coverage": "synthetic_concurrent_native_adapters_no_rpc_no_vendor_host",
            "initial_state_bytes": before_bytes,
            "unaccounted_input_count": fanout - observed,
            "retained_invocation_count": sum(sample["retained"] for sample in samples),
            "retained_ms": [sample["ms"] for sample in samples if sample["retained"]],
            "not_retained_ms": [sample["ms"] for sample in samples if not sample["retained"]],
            "initial_pending": before_pending,
            "final_pending": store.pending_outbox_count(workspace),
            "initial_quarantined": before_quarantine,
            "final_quarantined": store.quarantined_count(workspace),
            "accounting_before": dict(before),
            "accounting_after": dict(after),
            "p50_ms": percentile(50),
            "p95_ms": percentile(95),
            "p99_ms": percentile(99),
            "max_ms": values[-1],
            "samples": sorted(samples, key=lambda sample: sample["lane"]),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fanout", type=int, default=8, choices=range(1, 17))
    parser.add_argument("--host", choices=["codex", "claude", "cursor", "all"], default="all")
    parser.add_argument("--retained", action="store_true")
    args = parser.parse_args()
    hosts = ["codex", "claude", "cursor"] if args.host == "all" else [args.host]
    for host in hosts:
        print(json.dumps(run(host, args.fanout, args.retained), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

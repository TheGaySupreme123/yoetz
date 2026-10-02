"""Compare headline Yoetz operations with the Rust accelerator disabled and required.

Run from the repository root after ``rust/build-native.sh``::

    uv run python rust/bench/compare.py              # every scenario
    uv run python rust/bench/compare.py canonical    # scenarios whose name contains a word

Each mode runs in its own fresh interpreter (``YZ_NATIVE=0`` and ``YZ_NATIVE=require``) over the
same deterministic inputs, built with the test suite's own builders. Times are the minimum of
several runs, so they estimate the cost of the work rather than machine noise. Nothing here
touches a real Yoetz data directory: scenarios that start a CLI process get a fresh private root.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _best(operation: Callable[[], object], *, repeat: int = 5, number: int = 1) -> float:
    operation()
    samples: list[float] = []
    for _ in range(repeat):
        started = time.perf_counter()
        for _ in range(number):
            operation()
        samples.append((time.perf_counter() - started) / number)
    return min(samples)


def _canonical_document() -> object:
    sys.path.insert(0, str(REPO / "tests" / "unit" / "protocol"))
    from test_canonical_performance import (  # pyright: ignore[reportMissingImports]
        _realistic_document,  # pyright: ignore[reportUnknownVariableType]
    )

    return _realistic_document()  # pyright: ignore[reportUnknownVariableType]


def scenario_canonical() -> dict[str, float]:
    from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse

    document = _canonical_document()
    raw = canonical_encode(document)  # pyright: ignore[reportArgumentType]
    return {
        f"canonical: strict_json_parse ({len(raw) // 1024} KiB state)": _best(
            lambda: strict_json_parse(raw)
        ),
        f"canonical: canonical_encode ({len(raw) // 1024} KiB state)": _best(
            lambda: canonical_encode(document)  # pyright: ignore[reportArgumentType]
        ),
        f"canonical: canonical_digest ({len(raw) // 1024} KiB state)": _best(
            lambda: canonical_digest(document)  # pyright: ignore[reportArgumentType]
        ),
    }


def scenario_privacy_scan() -> dict[str, float]:
    from yoetz.observability.privacy import redact_sensitive_content, scan_for_sensitive_content

    lines: list[str] = []
    for index in range(6_000):
        lines.append(f"    result_{index} = compute(value, {index})  # step {index}")
        if index % 500 == 0:
            lines.append(f'    api_token = "tok_{index:08d}abcdefabcdefabcdef"')
    text = ("\n".join(lines)).encode()[: 400 * 1024]
    return {
        "privacy: scan_for_sensitive_content (400 KiB source)": _best(
            lambda: scan_for_sensitive_content(text)
        ),
        "privacy: redact_sensitive_content (400 KiB source)": _best(
            lambda: redact_sensitive_content(text)
        ),
    }


def scenario_schema_catalog() -> dict[str, float]:
    from yoetz.protocol import schemas

    def cold_load() -> object:
        schemas._load_catalog_state.cache_clear()  # pyright: ignore[reportPrivateUsage]
        return schemas._load_catalog_state()  # pyright: ignore[reportPrivateUsage]

    return {"schemas: cold catalog load (216 schemas)": _best(cold_load, repeat=3)}


def scenario_status_page() -> dict[str, float]:
    sys.path[:0] = [str(REPO / "tests"), str(REPO / "tests" / "integration" / "application")]
    import test_status_render_cost as harness  # pyright: ignore[reportMissingImports]

    class _Patch:
        def setattr(self, target: object, name: str, value: object) -> None:
            setattr(target, name, value)

    async def measure() -> dict[str, float]:
        ledger: Any = await harness.build_codex_status_ledger()
        processes: Any = harness._Processes(_Patch())
        stages, _ = await harness._cursor_page_stages(ledger, processes, 900_000)
        status, _ = harness._closure_status(ledger, 970_000, processes)
        started = time.process_time()
        await harness.prepare_closure(
            status,
            ledger.started.session_id,
            ledger.started.writer_id,
            harness.Selection(),
        )
        closure = time.process_time() - started
        return {
            "status: cached 100-row evidence page, all stages (CPU)": sum(stages.values()),
            "status: closure-prepare of a ~1,000-event ledger (CPU)": closure,
        }

    return asyncio.run(measure())


def scenario_semantic_case() -> dict[str, float]:
    sys.path.insert(0, str(REPO / "tests"))
    from builders.large_semantic_cases import large_case  # pyright: ignore[reportMissingImports]
    from yoetz.application import semantic_case as module
    from yoetz.application.check import (
        CheckScope,
        allocate_findings,
        prior_finding_ids,
        run_deterministic_policies,
    )
    from yoetz.domain.privacy import ReviewContextProfile, ReviewSelectionPolicy
    from yoetz.protocol.ids import new_id

    class _Ids:
        def new(self, kind: Any) -> Any:
            return new_id(kind)

    frozen: Any = large_case(obligation_count=40, claim_count=30, evidence_count=20)
    assessments, _ = run_deterministic_policies(
        frozen, CheckScope((), ()), ("research-evidence/0.1.0", "work-integrity/0.1.0")
    )
    findings = allocate_findings(
        _Ids(), tuple(item.candidate for item in assessments), prior_finding_ids(frozen.projection)
    )
    profile = ReviewContextProfile.EXPANDED
    case = module.build_semantic_case(
        case_id="cas_10000000-0000-4000-8000-000000000001",
        frozen_case=frozen,
        dependency_digest="sha256:" + "b" * 64,
        findings=findings,
        review_context_profile=profile,
        review_selection=ReviewSelectionPolicy.for_profile(profile),
        policy_id="pvy_10000000-0000-4000-8000-000000000001",
        policy_version="1",
        prepared_byte_ceiling=None,
    )
    envelope = module.bounded_case_envelope(case)
    original = module.MAX_EGRESS_ENVELOPE_BYTES
    module.MAX_EGRESS_ENVELOPE_BYTES = int(len(envelope) * 0.4)
    try:
        bounded = _best(lambda: module.bounded_case_envelope(case), repeat=3)
    finally:
        module.MAX_EGRESS_ENVELOPE_BYTES = original
    return {"semantic case: bound a large review envelope to 40%": bounded}


def scenario_replay() -> dict[str, float]:
    sys.path.insert(0, str(REPO / "tests"))
    from integration.application import (  # pyright: ignore[reportMissingImports]
        test_ledger_replay_bench as bench,
    )
    from integration.application import (  # pyright: ignore[reportMissingImports]
        test_respond_status_receipt as helpers,
    )
    from yoetz.kernel import reducers

    async def build() -> Any:
        app, runtime, _ = helpers._build_app(seed_offset=77, ledger_backend="memory")
        started, checked, _ = await helpers._bootstrap_finding(app, seed=7700)
        head = checked.result_frontier
        for offset in range(0, 1_000, 100):
            head = await bench._drain_many(
                app, runtime, started, seed=100_000 + offset, expected=head.sequence, count=100
            )
        ledger, _ = next(iter(runtime.resources.values()))
        return ledger._state.records

    records = asyncio.run(build())
    # Fresh record objects per run, as a restart or reload presents them: replay keeps the last
    # replayed tuple and continues from it when the new one starts with the identical objects,
    # which would turn a repeated replay of the same tuple into a warm extension.
    fresh = iter([tuple(copy.copy(record) for record in records) for _ in range(4)])

    def cold() -> object:
        return reducers.replay_with_index(next(fresh))

    return {f"kernel: genesis replay of a {len(records)}-record ledger": _best(cold, repeat=3)}


def scenario_hooks() -> dict[str, float]:
    from yoetz.cli import hook_io, observe_hooks

    items = [
        {
            "id": f"item-{index}",
            "path": f"/work/repo/src/module_{index}.py",
            "score": 0.5 + index / 1000,
            "lines": [index, index + 1, index + 2],
            "meta": {"kind": "match", "offset": index * 7, "weight": 1.25},
        }
        for index in range(3_740)
    ]
    body = json.dumps(
        {
            "hook_event_name": "postToolUse",
            "conversation_id": "c-1",
            "generation_id": "g-1",
            "session_id": "s-1",
            "tool_name": "Grep",
            "duration": 12.75,
            "workspace_roots": ["/work/repo"],
            "tool_input": {"pattern": "def ", "path": "/work/repo"},
            "tool_output": {"results": items},
        }
    ).encode()
    lines: list[str] = []
    index = 0
    while sum(len(line) + 1 for line in lines) < 256 * 1024:
        lines.append(f"*** Update File: /work/repo/src/pkg/file_{index}.py")
        lines.append("@@ def handler(value):")
        lines.extend(
            f"-    old = compute(value, {n})\n+    new = compute(value, {n} + 1)" for n in range(6)
        )
        index += 1
    patch = "\n".join(lines)[: 256 * 1024]
    sanitize = getattr(observe_hooks, "_sanitize_patch_paths")
    return {
        f"hooks: Cursor hook ingress ({len(body) // 1024} KiB body)": _best(
            lambda: hook_io.read_cursor_hook_ingress(body)
        ),
        "hooks: sanitize a 256 KiB apply_patch": _best(lambda: sanitize(patch, "/work/repo")),
    }


def _private_root() -> Path:
    base = Path.home() / ".yz-native-bench"
    base.mkdir(mode=0o700, exist_ok=True)
    root = base / secrets.token_hex(4)
    root.mkdir(mode=0o700)
    (root / "home").mkdir(mode=0o700)
    return root


def scenario_process() -> dict[str, float]:
    python = sys.executable

    def run(argv: list[str], payload: bytes = b"") -> Callable[[], object]:
        def once() -> object:
            root = _private_root()
            try:
                environment = {
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("YOETZ_") and key not in {"PYTHONPATH", "PYTHONSTARTUP"}
                }
                environment.update({"HOME": str(root / "home"), "YOETZ_ISOLATED_ROOT": str(root)})
                return subprocess.run(
                    argv, input=payload, capture_output=True, env=environment, timeout=120
                )
            finally:
                shutil.rmtree(root, ignore_errors=True)

        return once

    hook = [
        python,
        "-m",
        "yoetz",
        "hooks",
        "claude-observe",
        "--event",
        "PreToolUse",
        "--observation-profile",
        "claude-code-ordinary-observation-v1",
    ]
    return {
        "process: import yoetz.mcp.server (wall)": _best(
            run([python, "-c", "import yoetz.mcp.server"]), repeat=15
        ),
        "process: one Claude Code PreToolUse hook (wall)": _best(
            run(hook, b'{"session_id":"native-bench"}'), repeat=15
        ),
    }


SCENARIOS: dict[str, Callable[[], dict[str, float]]] = {
    "canonical": scenario_canonical,
    "privacy": scenario_privacy_scan,
    "schemas": scenario_schema_catalog,
    "status": scenario_status_page,
    "semantic": scenario_semantic_case,
    "replay": scenario_replay,
    "hooks": scenario_hooks,
    "process": scenario_process,
}


def _worker(names: list[str]) -> None:
    from yoetz._native import native

    results: dict[str, float] = {}
    for name in names:
        results.update(SCENARIOS[name]())
    print(json.dumps({"native": native is not None, "results": results}))


def _run_mode(mode: str, names: list[str]) -> dict[str, float]:
    environment = dict(os.environ, YZ_NATIVE=mode)
    completed = subprocess.run(
        [sys.executable, __file__, "--worker", *names],
        capture_output=True,
        env=environment,
        cwd=REPO,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr.decode(errors="replace"))
        raise SystemExit(f"worker failed in YZ_NATIVE={mode}")
    report = json.loads(completed.stdout.decode().strip().splitlines()[-1])
    expected = mode != "0"
    if report["native"] is not expected:
        raise SystemExit(f"YZ_NATIVE={mode} ran with native={report['native']}")
    return report["results"]


def _format(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms" if seconds >= 0.001 else f"{seconds * 1_000_000:.0f} µs"


def main() -> None:
    if sys.argv[1:2] == ["--worker"]:
        _worker(sys.argv[2:])
        return
    wanted = sys.argv[1:]
    names = [name for name in SCENARIOS if not wanted or any(word in name for word in wanted)]
    python = _run_mode("0", names)
    native = _run_mode("require", names)
    print("| Operation | Pure Python | Rust accelerator | Speedup |")
    print("|---|---:|---:|---:|")
    for label, before in python.items():
        after = native[label]
        print(f"| {label} | {_format(before)} | {_format(after)} | {before / after:.1f}x |")


if __name__ == "__main__":
    main()

"""Status render-cost harness, pre-change golden, and CI regression bounds for issue #916.

The DeepSWE v2 run measured a 100-row ``status view=evidence`` page at ~2.1 s even on a cached
cursor page (~0.5 s fixed plus ~18 ms per returned row), and ``closure-prepare`` at 16-55 s. The
harness renders pages of a synthetic ~1,000-event Codex-shaped ledger
(``builders.status_render_cost``) through the exact path one MCP ``status`` response takes:

1. ``status`` itself (the cached projection query and the page-model validation);
2. the daemon's post-commit privacy projection (``project_result_for_client``: leaf walk and
   classification, the never-send scan, the audit encodings, the local-disclosure receipt write
   into the SQLite privacy catalog, and the public result model);
3. the control envelope: the daemon's success-body check, frame encode, client decode and parse;
4. the MCP bridge: wire dump, result-model revalidation and the tool result rendering.

``test_status_render_matches_pre_change_golden`` pins every rendered byte and every durable
receipt to ``tests/fixtures/status-render-cost/golden.json``, which was generated from the code
*before* the #916 optimizations. The optimizations change cost only: projected pages, MCP text,
receipts, audit subjects (field decisions and commitments) and the ``closure-prepare`` inventory
must stay byte-identical. Regenerate it only for an intended output change, from the owning
change, with::

    YOETZ_STATUS_RENDER_GOLDEN=write uv run pytest \
        tests/integration/application/test_status_render_cost.py -k golden

The cost tests never compare wall-clock time with a fixed threshold. They count work that is
deterministic on any runner, and compare timings only as ratios against a baseline measured in
the same run, with generous bounds, so a slow or loaded CI runner cannot flip them. Run them with
``-s`` to print the stage profile.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final, cast

import pytest
from mcp import types

from builders.start_application import protocol_id
from builders.status_render_cost import (
    CLOSURE_VIEWS,
    CodexStatusLedger,
    build_codex_status_ledger,
)
from yoetz.cli.closure import Selection, prepare_closure
from yoetz.mcp.server import result_from_public_model
from yoetz.ports.control import ControlMethod, ControlResult
from yoetz.protocol import schemas as schemas_module
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.models import StatusRequest, StatusResult, public_model_to_wire
from yoetz.service.control_protocol import (
    decode_control_frame,
    encode_control_frame,
    parse_control_result,
    validate_result,
)

pytestmark = pytest.mark.anyio


_GOLDEN: Final = Path(__file__).parents[2] / "fixtures" / "status-render-cost" / "golden.json"
_GOLDEN_SCHEMA: Final = "yoetz.test.status-render-golden/1"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class _Processes:
    """Model the service and the MCP bridge (or CLI) as the two processes they are.

    Each process owns its schema verdict memory; sharing one in-process memory between the
    service and client halves would make the harness optimistic. Harmless before #916, when there
    was no memory to model.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker_type = getattr(schemas_module, "_ValidityChecker", None)
        self._checkers: dict[str, object] = {}
        self.current = "service"
        if checker_type is None:
            return
        state = schemas_module._load_catalog_state()  # pyright: ignore[reportPrivateUsage]
        build = cast(Callable[[object], object], getattr(checker_type, "build"))
        self._checkers = {"service": build(state), "client": build(state)}
        monkeypatch.setattr(
            schemas_module, "_validity_checker", lambda: self._checkers[self.current]
        )

    def enter(self, process: str) -> None:
        self.current = process


def _service_frame(model: StatusResult, seed: int) -> bytes:
    """The daemon's success-body check and the frame it writes to the control socket."""

    result = ControlResult(
        protocol_version="1.0",
        rpc_id=protocol_id("rpc_", seed),
        service_instance_id=protocol_id("svc_", seed + 1),
        service_generation="1",
        method=ControlMethod.STATUS,
        outcome="ok",
        body=model,
    )
    validate_result(result)
    return encode_control_frame(result)


def _client_parse(frame: bytes) -> StatusResult:
    """The client's frame decode and typed parse."""

    body = parse_control_result(decode_control_frame(frame)).body
    assert type(body) is StatusResult
    return body


def _bridge(model: StatusResult) -> tuple[dict[str, JsonValue], types.CallToolResult]:
    """What the MCP bridge does with a parsed status result before returning it to the host."""

    wire = public_model_to_wire(model)
    validated = StatusResult.model_validate(wire)
    return wire, result_from_public_model(validated, host_profile="codex")


async def _mcp_page(
    ledger: CodexStatusLedger,
    view: str,
    seed: int,
    cursor: str | None = None,
    processes: _Processes | None = None,
) -> tuple[dict[str, JsonValue], types.CallToolResult]:
    if processes is not None:
        processes.enter("service")
    model = await ledger.project_status(ledger.status_body(view, seed, cursor=cursor), seed + 1)
    frame = _service_frame(model, seed + 3)
    if processes is not None:
        processes.enter("client")
    return _bridge(_client_parse(frame))


def _omission_reasons(wire: dict[str, JsonValue]) -> tuple[tuple[str, str], ...]:
    """(pointer, reason) for every omitted pointer the projection names."""

    projection = cast(dict[str, JsonValue], wire["privacy_projection"])
    reasons: list[tuple[str, str]] = []
    for pointer in cast(list[str], projection["omitted_pointers"]):
        value: JsonValue = wire
        for segment in pointer.removeprefix("/").split("/"):
            if isinstance(value, list):
                value = cast(list[JsonValue], value)[int(segment)]
            else:
                assert isinstance(value, dict)
                value = value[segment]
        assert isinstance(value, dict)
        marker = value
        assert marker["omitted"] is True
        reasons.append((pointer, cast(str, marker["reason"])))
    return tuple(reasons)


def _closure_status(
    ledger: CodexStatusLedger, first_seed: int, processes: _Processes | None = None
) -> tuple[Callable[[StatusRequest], Awaitable[StatusResult]], list[str]]:
    """The CLI's ``client.status``: service projection, control frame, client parse.

    Returns the callable and the list of views it was asked for, in call order.
    """

    calls: list[str] = []

    async def status(request: StatusRequest) -> StatusResult:
        calls.append(request.view)
        seed = first_seed + len(calls) * 10
        if processes is not None:
            processes.enter("service")
        model = await ledger.project_status(public_model_to_wire(request), seed)
        frame = _service_frame(model, seed + 3)
        if processes is not None:
            processes.enter("client")
        return _client_parse(frame)

    return status, calls


async def _golden_record(ledger: CodexStatusLedger) -> dict[str, JsonValue]:
    pages: dict[str, JsonValue] = {}
    reasons: Counter[str] = Counter()
    never_send: list[str] = []
    seed = 700_000
    for view in ("compact", *CLOSURE_VIEWS):
        cursor: str | None = None
        index = 0
        while True:
            seed += 10
            wire, tool = await _mcp_page(ledger, view, seed, cursor)
            label = f"{view}/{index}"
            text = cast(types.TextContent, tool.content[0]).text
            pages[label] = {
                "rows": len(
                    cast(list[JsonValue], cast(dict[str, JsonValue], wire["page"])["items"])
                )
                if view != "compact"
                else None,
                "wire": _digest(canonical_encode(wire)),
                "mcp_text": _digest(text.encode("utf-8")),
            }
            for pointer, reason in _omission_reasons(wire):
                reasons[reason] += 1
                if reason == "never_send_redacted":
                    never_send.append(f"{label}{pointer}")
            cursor = cast(str | None, cast(dict[str, JsonValue], wire["page"]).get("next_cursor"))
            index += 1
            if cursor is None:
                break
    receipts: list[JsonValue] = [
        {"request_id": request, "audit_subject": _digest(subject), "receipt": _digest(receipt)}
        for request, subject, receipt in ledger.receipt_rows()
    ]
    inventory = await prepare_closure(
        _closure_status(ledger, 800_000)[0],
        ledger.started.session_id,
        ledger.started.writer_id,
        Selection(),
    )
    rows = cast(dict[str, list[JsonValue]], inventory["inventory"])
    return {
        "schema": _GOLDEN_SCHEMA,
        "ledger": {
            "head_sequence": ledger.head.sequence,
            "hook_calls": ledger.hook_calls,
            "agent_rows": ledger.agent_rows,
        },
        "pages": pages,
        "omission_reasons": dict(sorted(reasons.items())),
        "never_send_redacted": cast(list[JsonValue], never_send),
        "receipts": receipts,
        "closure_inventory": {
            "digest": _digest(canonical_encode(inventory)),
            "rows": {view: len(rows[view]) for view in CLOSURE_VIEWS},
        },
    }


async def test_status_render_matches_pre_change_golden() -> None:
    ledger = await build_codex_status_ledger()
    record = await _golden_record(ledger)
    # The fixed ledger must exercise both omission reasons, on another writer's row (the hook
    # evidence description) and on a self-authored row (the agent evidence reference), or the
    # golden would not pin the never-send distinction at all.
    assert set(cast(dict[str, int], record["omission_reasons"])) == {
        "local_disclosure_not_authorized",
        "never_send_redacted",
    }
    redacted = cast(list[str], record["never_send_redacted"])
    assert any(item.startswith("evidence/") and item.endswith("/description") for item in redacted)
    assert any(item.startswith("evidence/") and item.endswith("/reference") for item in redacted)
    if os.environ.get("YOETZ_STATUS_RENDER_GOLDEN") == "write":
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("golden regenerated")
    expected = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert expected["schema"] == _GOLDEN_SCHEMA
    # Compare section by section so a failure names what diverged.
    for key in ("ledger", "omission_reasons", "never_send_redacted", "closure_inventory"):
        assert record[key] == expected[key], key
    assert sorted(cast(dict[str, JsonValue], record["pages"])) == sorted(expected["pages"])
    for label, page in cast(dict[str, JsonValue], record["pages"]).items():
        assert page == expected["pages"][label], label
    assert len(cast(list[JsonValue], record["receipts"])) == len(expected["receipts"])
    for index, receipt in enumerate(cast(list[JsonValue], record["receipts"])):
        assert receipt == expected["receipts"][index], f"receipt {index}"

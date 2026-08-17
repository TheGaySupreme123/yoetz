"""Latency fence for the canonical codec on a realistically-sized document (#290).

The observation state file reaches 1 MiB, and every hook pass parses it once
and encodes it at least twice. The per-character Python loops that used to
implement string validation/escaping made those operations ~70x slower than
the stdlib on the same bytes, which dominated the hook 'store' stage. A
fixture-sized document passes any implementation trivially, so this fence
measures a ~1 MiB document and bounds the cost relative to the stdlib codec
on the same machine — immune to CI hardware variance, generous enough to
never flap, and far below the regressed ratio.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse

# The regressed implementation measured ~30-70x stdlib; the current one ~5-10x.
_MAX_STDLIB_RATIO = 20.0
# Below this absolute cost the ratio is noise-dominated and irrelevant to the
# hook budget either way.
_ABSOLUTE_FLOOR_SECONDS = 0.080


def _realistic_document() -> dict[str, JsonValue]:
    """~1 MiB of state-file-shaped JSON: hashes, tokens, and some escapes."""

    digest = "hmac-sha256:" + "ab" * 32
    envelopes: list[JsonValue] = [
        {
            "session_commitment": digest,
            "event_kind": "PreToolUse",
            "source_identity": f"hook:{index}:{digest}",
            "structural_payload": {
                "tool_name": "shell",
                "tool_call_id": f"call_{index}",
                "command": 'rg --json "needle" ./src\n' * 4,
            },
            "gap_codes": ["unpaired_event"],
        }
        for index in range(900)
    ]
    return {
        "dedup": [f"{digest}:{index}" for index in range(2800)],
        "envelopes": envelopes,
        "quarantine": envelopes[:300],
        "stream_partials": {digest: "b64:" + "QUJD" * 4_000},
    }


def _best_of(operation: Callable[[], object], runs: int = 3) -> float:
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        operation()
        best = min(best, time.perf_counter() - started)
    return best


def test_parse_and_encode_stay_within_ratio_of_stdlib_on_large_state() -> None:
    document = _realistic_document()
    raw = canonical_encode(document)
    assert len(raw) > 700_000

    stdlib_parse = _best_of(lambda: json.loads(raw))
    strict_parse = _best_of(lambda: strict_json_parse(raw))
    assert strict_parse <= max(stdlib_parse * _MAX_STDLIB_RATIO, _ABSOLUTE_FLOOR_SECONDS)

    stdlib_encode = _best_of(
        lambda: json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    )
    canonical = _best_of(lambda: canonical_encode(document))
    assert canonical <= max(stdlib_encode * _MAX_STDLIB_RATIO, _ABSOLUTE_FLOOR_SECONDS)


def test_fast_string_paths_are_output_identical() -> None:
    """Escape-needing and plain strings encode byte-identically to the spec."""

    tricky = {
        "plain": "ordinary token-text_1234:/+-",
        "escapes": 'quote " backslash \\ tab \t newline \n bell \x07 unit \x1f',
        "unicode": "διακριτικά — em—dash é中文",
        "empty": "",
    }
    encoded = canonical_encode(tricky)
    assert encoded == (
        b'{"empty":"","escapes":"quote \\" backslash \\\\ tab \\t newline \\n '
        b'bell \\u0007 unit \\u001f","plain":"ordinary token-text_1234:/+-",'
        b'"unicode":"\xce\xb4\xce\xb9\xce\xb1\xce\xba\xcf\x81\xce\xb9\xcf\x84\xce\xb9\xce\xba'
        b'\xce\xac \xe2\x80\x94 em\xe2\x80\x94dash \xc3\xa9\xe4\xb8\xad\xe6\x96\x87"}'
    )
    assert strict_json_parse(encoded) == tricky

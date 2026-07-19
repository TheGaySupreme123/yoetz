from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import cast

from helpers.child import (
    ChildLimits,
    ChildResult,
    ChildSpec,
    assert_no_owned_children,
    communicate_bounded,
    spawn_installed,
)

_PASSPHRASE_CANARY = b"unlock-canary-value-2026"

_VALIDATION_PROBE = r"""
import base64, json, sys
from yoetz.service.confidential_protocol import ConfidentialProtocolError, validate_passphrase_buffer

cases = json.loads(sys.stdin.buffer.read())
results = []
for encoded in cases:
    secret = bytearray(base64.b64decode(encoded))
    view = memoryview(secret)
    try:
        validate_passphrase_buffer(view)
        results.append("accepted")
    except ConfidentialProtocolError as exc:
        results.append(exc.reason)
    finally:
        view.release()
        secret[:] = b"\x00" * len(secret)
print(json.dumps({"results": results}, separators=(",", ":"), sort_keys=True))
"""

_ORDINARY_CONTROL_PROBE = r"""
import json, os, sys
from yoetz.domain.values import JsonObject
from yoetz.protocol.canonical import strict_json_parse
from yoetz.service.control_protocol import ControlProtocolError, parse_control_request

payload = bytearray(sys.stdin.buffer.read())
try:
    parsed = strict_json_parse(bytes(payload))
    try:
        parse_control_request(JsonObject(parsed))
        reason = "unexpected_accept"
    except ControlProtocolError as exc:
        reason = exc.reason
    secret = bytes(payload).split(b'"secret":"', 1)[1].split(b'"', 1)[0]
    argv_leak = any(secret in item.encode("utf-8") for item in sys.argv)
    env_leak = any(secret in value.encode("utf-8", "surrogateescape") for value in os.environ.values())
finally:
    payload[:] = b"\x00" * len(payload)
print(json.dumps({"argv_leak": argv_leak, "env_leak": env_leak, "reason": reason}, separators=(",", ":"), sort_keys=True))
"""


def _run_probe(script: str, input_bytes: bytes) -> ChildResult:
    spec = ChildSpec(
        executable=Path(sys.executable),
        argv=("-I", "-c", script),
        limits=ChildLimits(wall_time_seconds=20.0, max_output_bytes=65_536),
    )
    handle = spawn_installed(
        spec,
        {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "VIRTUAL_ENV": sys.prefix},
    )
    result = communicate_bounded(handle, input_bytes)
    assert_no_owned_children(result.temp_root)
    assert result.limit_verdict == "passed"
    assert result.exit_code == 0
    assert result.signal is None
    assert result.stderr == b""
    return result


def _json_output(result: ChildResult) -> dict[str, object]:
    return cast(dict[str, object], json.loads(result.stdout))


def test_passphrase_bytes_are_validated_exactly_in_an_isolated_process() -> None:
    accepted = (_PASSPHRASE_CANARY, "correct horse battery 🔒".encode())
    rejected = (
        b"x" * 15,
        b"x" * 1_025,
        b"sixteen-bytes-ok\x00",
        b"sixteen-bytes-ok\n",
        b"sixteen-bytes-ok\r",
        b"x" * 16 + b"\xff",
    )
    cases = [base64.b64encode(value).decode("ascii") for value in (*accepted, *rejected)]
    result = _run_probe(_VALIDATION_PROBE, json.dumps(cases).encode("ascii"))
    assert _json_output(result) == {
        "results": ["accepted", "accepted", *("secret_rejected" for _ in rejected)]
    }
    assert _PASSPHRASE_CANARY not in result.stdout


def test_ordinary_control_cannot_name_or_carry_an_unlock_secret() -> None:
    request = {
        "body": {"secret": _PASSPHRASE_CANARY.decode("ascii")},
        "kind": "call",
        "method": "vault_unlock",
        "protocol_version": "1.0",
        "rpc_id": "rpc_11111111-1111-4111-8111-111111111111",
        "service_generation": "1",
        "service_instance_id": "svc_22222222-2222-4222-8222-222222222222",
    }
    encoded = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("ascii")
    result = _run_probe(_ORDINARY_CONTROL_PROBE, encoded)
    observed = _json_output(result)
    assert observed["reason"] in {"frame_invalid", "method_forbidden"}
    assert observed["argv_leak"] is False
    assert observed["env_leak"] is False
    assert _PASSPHRASE_CANARY not in result.stdout + result.stderr

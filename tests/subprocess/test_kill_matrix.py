"""Closed 16-boundary crash matrix and deterministic controller certification."""

from __future__ import annotations

import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers.child import ChildLimits, ChildSpec, assert_no_owned_children
from helpers.fault_controller import (
    FAULT_DESCRIPTOR_FD,
    CommitClass,
    FaultMode,
    FaultPlan,
    FaultPoint,
    arm_fault,
    wait_and_trigger,
)

_INSTRUMENTED_CHILD = rf"""
import json, os, socket
stream = socket.socket(fileno={FAULT_DESCRIPTOR_FD})
buffer = bytearray()
def receive():
    while b'\n' not in buffer:
        chunk = stream.recv(4096)
        if not chunk: raise SystemExit(72)
        buffer.extend(chunk)
    line, _, tail = buffer.partition(b'\n')
    buffer[:] = tail
    return json.loads(line)
def send(kind, arm, ordinal):
    body = {{"kind":kind,"nonce":arm["nonce"],"ordinal":ordinal,"point":arm["point"]}}
    stream.sendall(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("ascii") + b'\n')
arm = receive()
send("armed", arm, 1)
send("reached", arm, 2)
trigger = receive()
if trigger["kind"] != "trigger": raise SystemExit(73)
os._exit({{"abrupt_exit":74,"injected_io_error":75,"broken_pipe":76}}[trigger["mode"]])
"""


@dataclass(frozen=True, slots=True)
class FaultCase:
    point: FaultPoint
    operation_fixture: str
    mode: FaultMode
    expected_class: CommitClass
    recovery_command: str
    durable_oracle: str
    platform_applicable: bool = True


_CASES = (
    FaultCase(
        FaultPoint.BEFORE_OBJECT_CREATION,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "reopen_retry",
        "no_effect",
    ),
    FaultCase(
        FaultPoint.AFTER_PARTIAL_CIPHERTEXT,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "reopen_retry",
        "orphan_only",
    ),
    FaultCase(
        FaultPoint.AFTER_FILE_FSYNC_BEFORE_RENAME,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "reopen_retry",
        "orphan_only",
    ),
    FaultCase(
        FaultPoint.AFTER_RENAME_BEFORE_DIRECTORY_FSYNC,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "reopen_retry",
        "object_unreferenced",
    ),
    FaultCase(
        FaultPoint.AFTER_DURABLE_OBJECT_BEFORE_BEGIN,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "reopen_retry",
        "object_unreferenced",
    ),
    FaultCase(
        FaultPoint.AFTER_SEQUENCE_ALLOCATION,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "reopen_retry",
        "no_partial_batch",
    ),
    FaultCase(
        FaultPoint.AFTER_EVENT_INSERT,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "reopen_retry",
        "no_partial_batch",
    ),
    FaultCase(
        FaultPoint.AFTER_PROJECTION_UPDATE,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "replay_retry",
        "no_partial_batch",
    ),
    FaultCase(
        FaultPoint.BEFORE_COMMIT,
        "publish",
        FaultMode.SIGKILL,
        "pre_commit",
        "replay_retry",
        "not_acknowledged",
    ),
    FaultCase(
        FaultPoint.AFTER_COMMIT_BEFORE_RESPONSE,
        "publish",
        FaultMode.SIGKILL,
        "post_commit",
        "replay_retry",
        "one_durable_result",
    ),
    FaultCase(
        FaultPoint.DURING_MCP_STDOUT_RESPONSE,
        "publish",
        FaultMode.BROKEN_PIPE,
        "post_commit",
        "reconnect_retry",
        "one_durable_result",
    ),
    FaultCase(
        FaultPoint.DURING_CHECKPOINT,
        "checkpoint",
        FaultMode.SIGKILL,
        "maintenance",
        "reopen_replay",
        "canonical_unchanged",
    ),
    FaultCase(
        FaultPoint.DURING_BACKUP_FINALIZATION,
        "backup",
        FaultMode.SIGKILL,
        "maintenance",
        "backup_verify",
        "old_or_new_complete",
    ),
    FaultCase(
        FaultPoint.DURING_CATALOG_ROUTE_SWITCH,
        "migrate",
        FaultMode.SIGKILL,
        "maintenance",
        "route_recover",
        "old_or_new_complete",
    ),
    FaultCase(
        FaultPoint.AFTER_PROVIDER_RESPONSE,
        "check",
        FaultMode.SIGKILL,
        "semantic",
        "reopen_retry",
        "attempt_not_selected",
    ),
    FaultCase(
        FaultPoint.BEFORE_SEMANTIC_FRESHNESS_VALIDATION,
        "check",
        FaultMode.SIGKILL,
        "semantic",
        "reopen_retry",
        "stale_not_selected",
    ),
)


def test_fault_case_table_is_closed_complete_and_classified() -> None:
    assert len(_CASES) == 16
    assert tuple(case.point for case in _CASES) == tuple(FaultPoint)
    assert len({case.point for case in _CASES}) == 16
    assert all(case.platform_applicable for case in _CASES)
    assert [case.expected_class for case in _CASES[:9]] == ["pre_commit"] * 9
    assert [case.expected_class for case in _CASES[9:11]] == ["post_commit"] * 2
    assert [case.expected_class for case in _CASES[11:14]] == ["maintenance"] * 3
    assert [case.expected_class for case in _CASES[14:]] == ["semantic"] * 2
    assert all(case.recovery_command and case.durable_oracle for case in _CASES)

    with pytest.raises(ValueError, match="fault_plan_class_invalid"):
        FaultPlan(
            point=FaultPoint.BEFORE_COMMIT,
            mode=FaultMode.SIGKILL,
            operation_identity="operation:publish",
            request_identity="request:wrong-class",
            deadline_seconds=10.0,
            expected_class="post_commit",
        )


@pytest.mark.fault
@pytest.mark.kill_matrix
@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.point.value)
def test_controller_faults_once_at_each_acknowledged_boundary(case: FaultCase) -> None:
    """Certify the matrix controller; product durability oracles run in the release profile."""

    plan = FaultPlan(
        point=case.point,
        mode=case.mode,
        operation_identity=f"operation:{case.operation_fixture}",
        request_identity=f"request:{case.point.value}",
        deadline_seconds=10.0,
        expected_class=case.expected_class,
    )
    armed = arm_fault(
        ChildSpec(
            executable=Path(sys.executable),
            argv=("-I", "-c", _INSTRUMENTED_CHILD),
            limits=ChildLimits(wall_time_seconds=15.0, max_output_bytes=16_384),
        ),
        plan,
        {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    observation = wait_and_trigger(armed)
    assert observation.marker_ordinal == 2
    assert observation.reason_code == f"fault_triggered:{case.mode.value}"
    if case.mode is FaultMode.SIGKILL:
        assert observation.signal == signal.SIGKILL
        assert observation.exit_code is None
    else:
        assert observation.signal is None
        assert observation.exit_code in {74, 75, 76}
    assert observation.armed_monotonic <= observation.reached_monotonic
    assert observation.reached_monotonic <= observation.terminated_monotonic
    assert_no_owned_children(armed.handle.temp_root)

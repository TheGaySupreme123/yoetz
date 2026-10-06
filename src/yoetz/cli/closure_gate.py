"""The Stop-time closure gate (issue #977, ADR-033).

When an agent stops while the mapped task's closure readiness still names work the agent can do,
the Stop hook continues it once with a message that names what remains. The decision reads only
the service's own ``status`` answer (``closure_readiness``), never the transcript or a file:

* it continues only for ``STOP_GATE_TOKENS`` in ``agent_actionable``: open obligations, an
  unacknowledged receipt-blocking finding, and the task facts the agent can repair (a planned
  verification not observed, failing or stale; a requested output Git would not deliver);
* once per ledger frontier and session: a second Stop at the same frontier passes, so an agent
  that cannot or will not act is never trapped; the host's own loop guard (``stop_hook_active``,
  Cursor ``loop_count``) is honoured before any service read;
* an obligation a recorded blocker decision names (authority, consent, credentials, an
  unobtainable dependency) is already a standing disclosure in readiness and never gates;
* while hook observations are still draining, the facts that depend on observed runs are not
  used to continue the agent.

The delivered text holds only closed tokens and counts. The once-per-frontier memory is a small
owner-only file in the observation state directory holding opaque session and frontier ids.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from yoetz import __version__
from yoetz.config.paths import ensure_owner_only_dir, state_dir
from yoetz.kernel.task_facts import (
    PLANNED_VERIFICATION_FAILED_GAP,
    PLANNED_VERIFICATION_NOT_OBSERVED_GAP,
    PLANNED_VERIFICATION_STALE_GAP,
    REQUESTED_OUTPUT_GIT_IGNORED_GAP,
    STOP_GATE_TOKENS,
)
from yoetz.ports.control import ControlClientKind
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import OperationFailureModel, StatusRequest

try:
    import fcntl
except ImportError:  # pragma: no cover - supported hook hosts are POSIX
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "ClosureGate",
    "ClosureGateUnavailable",
    "closure_gate_already_delivered",
    "closure_gate_from_readiness",
    "read_closure_gate",
    "record_closure_gate_delivered",
]

_FILE_NAME: Final = "closure-gate.json"
_LOCK_NAME: Final = "closure-gate.lock"
_MAX_SESSIONS: Final = 128
_STATUS_DEADLINE_MS: Final = 4_000
_OBSERVATION_DEPENDENT: Final = frozenset(
    {
        PLANNED_VERIFICATION_FAILED_GAP,
        PLANNED_VERIFICATION_NOT_OBSERVED_GAP,
        PLANNED_VERIFICATION_STALE_GAP,
    }
)
_ITEM_TEXT: Final[Mapping[str, str]] = {
    "obligations_open": "{open} open obligation(s)",
    "receipt_findings_unresolved": "{blocking} receipt-blocking finding(s) not repaired",
    "closing_review_required": (
        "the closing review has not run since your last change: run `check` with"
        " `final_review: true`"
    ),
    PLANNED_VERIFICATION_NOT_OBSERVED_GAP: (
        "a planned verification command (an obligation's requested command item) was never seen"
        " run exactly as recorded"
    ),
    PLANNED_VERIFICATION_FAILED_GAP: "a planned verification command failed on its latest run",
    PLANNED_VERIFICATION_STALE_GAP: (
        "a planned verification command last ran before your latest edit"
    ),
    REQUESTED_OUTPUT_GIT_IGNORED_GAP: (
        "a requested output file is git-ignored or excluded, so a diff-based delivery omits it"
    ),
}


class ClosureGateUnavailable(Exception):
    """The status read the gate needs did not answer; the agent is allowed to stop."""


@dataclass(frozen=True, slots=True)
class ClosureGate:
    """One Stop continuation: its once-per-frontier identity, its text and the closed items."""

    identity: str
    text: str
    items: tuple[str, ...]


def closure_gate_from_readiness(
    readiness: object,
    *,
    frontier_sequence: str,
    frontier_digest: str,
    observation_pending: bool,
) -> ClosureGate | None:
    """Decide the gate from one ``closure_readiness`` object; ``None`` means let the agent stop."""

    if getattr(readiness, "state", None) != "action_required":
        return None
    actionable = getattr(readiness, "agent_actionable", None)
    if not isinstance(actionable, (tuple, list)):
        return None
    present = {item for item in tuple(cast(tuple[object, ...], actionable)) if type(item) is str}
    items = tuple(
        token
        for token in STOP_GATE_TOKENS
        if token in present and not (observation_pending and token in _OBSERVATION_DEPENDENT)
    )
    if not items:
        return None
    open_count = str(getattr(readiness, "open_obligation_count", "?"))
    blocking_count = str(getattr(readiness, "receipt_blocking_finding_count", "?"))
    remaining = "; ".join(
        _ITEM_TEXT[token].format(open=open_count, blocking=blocking_count) for token in items
    )
    text = (
        f"Yoetz closure gate (shown once for ledger frontier {frontier_sequence}): closure still"
        f" requires action before you stop. Remaining: {remaining}. Finish the work: run each"
        " planned verification after your last edit (or correct the requested command to the one"
        " you run), write every requested output at its requested path (status"
        " view=obligations names them; an inconsistent input is not a reason to omit one, write a"
        " best-effort artifact and disclose the assumption), repair the receipt-blocking"
        " findings, then check and request the receipt. Stop without that only for a blocker"
        " outside your control (authority, consent, credentials, a dependency you cannot obtain):"
        " publish decision_recorded with affected_obligation_ids and the statement line"
        " yoetz-blocker:<authority|consent|credential|dependency_unavailable>, then say so in"
        " your final answer. Data the task says is recoverable is not such a blocker."
    )
    if observation_pending:
        text += " (Hook observations were still draining; observed-run facts were not used.)"
    return ClosureGate(f"{frontier_sequence}:{frontier_digest}", text, items)


def _status_request(session_id: str, writer_id: str, actor_id: str) -> StatusRequest:
    return StatusRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": new_id(IdKind.REQUEST),
            "actor": {"actor_id": actor_id, "actor_type": "harness"},
            "client": {"kind": "yoetz_cli", "version": __version__, "integration": "local_cli"},
            "session_id": session_id,
            "writer_id": writer_id,
            "view": "compact",
            "limit": "1",
            "at_frontier": None,
            "cursor": None,
        }
    )


async def read_closure_gate(
    *,
    session_id: str,
    writer_id: str,
    connect: Callable[[ControlClientKind], Awaitable[object]],
    actor_id: str,
    observation_pending: bool,
) -> ClosureGate | None:
    """Read the mapped session's compact status and decide the gate.

    ``None`` means the status was read and closure needs no continuation. A status the service
    could not answer raises ``ClosureGateUnavailable`` so the caller records it as unavailable;
    either way the agent may stop.
    """

    client: object | None = None
    try:
        client = await connect(ControlClientKind.CLI)
        status = getattr(client, "status", None)
        if not callable(status):
            raise ClosureGateUnavailable("status_unavailable")
        result = await cast(Callable[..., Awaitable[object]], status)(
            _status_request(session_id, writer_id, actor_id), deadline_ms=_STATUS_DEADLINE_MS
        )
        branch = getattr(result, "root", result)
        if isinstance(branch, OperationFailureModel):
            raise ClosureGateUnavailable("status_failed")
        head = getattr(branch, "head_frontier", None)
        sequence = getattr(head, "sequence", None)
        digest = getattr(head, "head_digest", None)
        if type(sequence) is not str or type(digest) is not str:
            raise ClosureGateUnavailable("status_unreadable")
        return closure_gate_from_readiness(
            getattr(branch, "closure_readiness", None),
            frontier_sequence=sequence,
            frontier_digest=digest,
            observation_pending=observation_pending,
        )
    except ClosureGateUnavailable:
        raise
    except Exception as exc:
        raise ClosureGateUnavailable("status_unavailable") from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                await cast(Callable[[], Awaitable[object]], close)()


def _paths(_state: Path | None) -> tuple[Path, Path, Path]:
    directory = (state_dir() if _state is None else _state) / "observation"
    return directory, directory / _FILE_NAME, directory / _LOCK_NAME


def _load(path: Path) -> dict[str, str]:
    try:
        if path.is_symlink():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in cast(dict[object, object], raw).items()
        if type(key) is str and type(value) is str
    }


def closure_gate_already_delivered(
    session_id: str, identity: str, *, _state: Path | None = None
) -> bool:
    _directory, path, _lock = _paths(_state)
    return _load(path).get(session_id) == identity


def record_closure_gate_delivered(
    session_id: str, identity: str, *, _state: Path | None = None
) -> bool:
    """Remember that this session's gate fired at this frontier. Never raises."""

    try:
        directory, path, lock_path = _paths(_state)
        ensure_owner_only_dir(directory)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            entries = _load(path)
            entries.pop(session_id, None)
            entries[session_id] = identity
            while len(entries) > _MAX_SESSIONS:
                entries.pop(next(iter(entries)))
            temporary = directory / f".{_FILE_NAME}.{os.getpid()}.tmp"
            temporary.write_text(json.dumps(entries, sort_keys=False), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            os.close(descriptor)
        return True
    except Exception:
        return False

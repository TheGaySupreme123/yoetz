"""Frozen agent-facing text for the closure-readiness checklist states (issue #913, ADR-032).

Like ``yoetz.protocol.recovery``, the *state token* travels and the text does not: every surface
(the MCP text summary, the CLI, the terminal interface and the shipped guidance) reconstructs the
same repository-authored sentence locally from ``closure_readiness.state`` and two service counts.
Nothing here is derived from a caller, a payload or model output.

The ``ready_with_limitations`` sentence is the owner-approved wording. It is an instruction to
stop and request the receipt, never a claim about the work: a standing limitation or an
acknowledged item is disclosed on the receipt exactly as recorded, and the receipt's verdict and
coverage stay bounded by them.
"""

from __future__ import annotations

from typing import Final, Literal

__all__ = [
    "READINESS_STATES",
    "readiness_directive",
]

type ReadinessState = Literal["action_required", "ready", "ready_with_limitations", "unknown"]

READINESS_STATES: Final[tuple[ReadinessState, ...]] = (
    "action_required",
    "ready",
    "ready_with_limitations",
    "unknown",
)

_ACTION_REQUIRED: Final = (
    "Work remains. Clear each agent-actionable item; standing limitations need no action."
)
_READY: Final = "Nothing further to do. Request the receipt."
_UNKNOWN: Final = (
    "Readiness is unknown at this frontier. Read status again once the projection is readable."
)


def _count(value: object) -> int:
    if type(value) is int and value >= 0:
        return value
    if type(value) is str and value.isascii() and value.isdigit():
        return int(value)
    raise ValueError("readiness_count_invalid")


def readiness_directive(state: str, *, standing: object = 0, acknowledged: object = 0) -> str:
    """Return the frozen sentence for one readiness state, filled only with service counts."""

    if state == "ready_with_limitations":
        return (
            f"Nothing further to do. {_count(standing)} standing limitation(s) and "
            f"{_count(acknowledged)} acknowledged item(s) will be disclosed on the receipt. "
            "Request the receipt."
        )
    if state == "ready":
        return _READY
    if state == "action_required":
        return _ACTION_REQUIRED
    if state == "unknown":
        return _UNKNOWN
    raise ValueError("readiness_state_invalid")

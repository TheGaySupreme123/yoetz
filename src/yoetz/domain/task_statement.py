"""The task statement: what the user asked for, kept apart from the agent's plan (issue #908).

The AI-powered reviewer judges a change against the task. Until this module existed the only
"goal" it received was the agent's own latest plan summary, so it could neither see a requirement
the plan dropped nor avoid asking for behaviour the user had excluded. A task statement is a
separate, source-labelled fact:

* ``agent_transcribed`` — the agent passed the user's request on ``start`` (or revised it with a
  plan event). It is the agent's account of the request, never a host observation.
* ``host_captured_user_prompt`` — the host captured the user's prompt. Reserved: no recorded path
  produces it yet, and captured prompt text never enters a review packet in this version.
* ``task_title_only`` — neither exists, so only the task title stands in.

Everything here is pure and replay-derived: the current statement is the newest one recorded in
the accepted prefix, and every earlier one stays in the ledger history.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from yoetz.domain.events import (
    AcceptedEvent,
    LedgerRecord,
    PlanPublishedPayload,
    PlanRevisedPayload,
    RedactionState,
    SessionOpenedPayload,
    SessionResumedPayload,
)
from yoetz.domain.values import EventId, event_id

__all__ = [
    "TASK_STATEMENT_GAPS",
    "TASK_STATEMENT_NOT_AUTHORIZED_GAP",
    "TASK_STATEMENT_NOT_SUPPLIED_GAP",
    "TASK_STATEMENT_UNAVAILABLE_GAP",
    "TASK_STATEMENT_SECTION",
    "RecordedTaskStatement",
    "TaskStatementSource",
    "current_task_statement",
    "may_carry_task_statement",
    "recorded_task_title",
    "task_statement_disclosure_text",
    "task_statement_gap_detail",
]

# The privacy review section and the packet section share one name, so the consent vocabulary and
# the packet the owner consented to cannot drift apart.
TASK_STATEMENT_SECTION: Final = "task_statement"

# The reviewer received no task statement at all. Always paired with exactly one reason below.
# A task title standing in is not this gap: its ``task_title_only`` source label discloses it.
TASK_STATEMENT_UNAVAILABLE_GAP: Final = "task_statement_unavailable"
# Neither an agent-supplied statement nor a readable task title is recorded for the task.
TASK_STATEMENT_NOT_SUPPLIED_GAP: Final = "task_statement_not_supplied"
# The approved privacy policy does not list the ``task_statement`` review section, so nothing is
# sent even when a statement is recorded. An approval that predates the section never covers it.
TASK_STATEMENT_NOT_AUTHORIZED_GAP: Final = "task_statement_not_authorized"
TASK_STATEMENT_GAPS: Final = frozenset(
    {
        TASK_STATEMENT_UNAVAILABLE_GAP,
        TASK_STATEMENT_NOT_SUPPLIED_GAP,
        TASK_STATEMENT_NOT_AUTHORIZED_GAP,
    }
)
_GAP_DETAILS: Final = {
    TASK_STATEMENT_UNAVAILABLE_GAP: (
        "The AI-powered reviewer did not receive the task statement, so it could not compare "
        "the work with what the user asked for."
    ),
    TASK_STATEMENT_NOT_SUPPLIED_GAP: (
        "No task statement or readable task title is recorded for this task; pass the user's "
        "request verbatim in start.task_statement."
    ),
    TASK_STATEMENT_NOT_AUTHORIZED_GAP: (
        "The approved privacy policy does not list the task_statement review section, so the "
        "recorded statement stayed local. Run 'yoetz --privacy' to review and approve it."
    ),
}


class TaskStatementSource(str, Enum):  # noqa: UP042 - exact wire enum base
    AGENT_TRANSCRIBED = "agent_transcribed"
    HOST_CAPTURED_USER_PROMPT = "host_captured_user_prompt"
    TASK_TITLE_ONLY = "task_title_only"


def task_statement_disclosure_text(
    *, section_selected: bool, channel_sends_task_description: bool, predates_section: bool
) -> str:
    """One plain sentence for ``privacy show``, ``yoetz --privacy`` and the policy draft.

    Fixed local wording only. It always says whether the agent's transcription of the user's
    request leaves the machine and that the host-captured prompt is not used.
    """

    captured = "The host-captured user prompt is never used."
    if section_selected and channel_sends_task_description:
        return (
            "sent. The agent's transcription of the user's request (or, when none was "
            "supplied, the task title) goes to the AI-powered reviewer as the task statement. "
            + captured
        )
    if section_selected:
        return (
            "not sent. The task_statement section is selected, but the review channel does not "
            "allow task_description, so the agent's transcription of the user's request stays "
            "local. " + captured
        )
    if predates_section:
        return (
            "not sent. This policy was approved before the task_statement section existed, so "
            "the agent's transcription of the user's request stays local. Approving the "
            "current recipe adds it. " + captured
        )
    return "not sent. The agent's transcription of the user's request stays local. " + captured


def task_statement_gap_detail(code: str) -> str | None:
    """Fixed receipt prose for one task-statement gap code, or ``None`` for any other code."""

    return _GAP_DETAILS.get(code)


@dataclass(frozen=True, slots=True)
class RecordedTaskStatement:
    """The newest agent-supplied statement at one frozen frontier."""

    text: str
    source_event_id: EventId
    source_family: str
    ingestion_sequence: int

    def __post_init__(self) -> None:
        if type(self.text) is not str or not self.text:
            raise ValueError("task_statement_invalid")
        object.__setattr__(self, "source_event_id", event_id(self.source_event_id))
        if self.source_family not in {
            "session_opened",
            "session_resumed",
            "plan_published",
            "plan_revised",
        }:
            raise ValueError("task_statement_invalid")
        if type(self.ingestion_sequence) is not int or self.ingestion_sequence < 1:
            raise ValueError("task_statement_invalid")

    @property
    def source(self) -> TaskStatementSource:
        # Every recorded statement arrives through the agent (``start`` or a plan event). Only a
        # future host-capture path may record anything else, and it will say so explicitly.
        return TaskStatementSource.AGENT_TRANSCRIBED


def current_task_statement(records: Iterable[LedgerRecord]) -> RecordedTaskStatement | None:
    """Return the newest readable statement in ``records``, oldest-first ledger order.

    A redacted or unreadable event carries nothing: its statement is skipped, never
    reconstructed, and the earlier statements remain what the ledger still holds.
    """

    current: RecordedTaskStatement | None = None
    for record in records:
        if type(record) is not AcceptedEvent or record.redaction is not RedactionState.PRESENT:
            continue
        payload = record.payload
        if type(payload) not in {
            SessionOpenedPayload,
            SessionResumedPayload,
            PlanPublishedPayload,
            PlanRevisedPayload,
        }:
            continue
        statement = getattr(payload, "task_statement", None)
        if type(statement) is not str:
            continue
        current = RecordedTaskStatement(
            text=statement,
            source_event_id=record.event_id,
            source_family=record.schema.name,
            ingestion_sequence=record.ledger.ingestion_sequence,
        )
    return current


# Event versions minted to carry a task statement. Nothing older can, so a review whose frozen
# frontier precedes the first of these in the ledger cannot have received a statement.
_STATEMENT_CAPABLE_SCHEMAS: Final = frozenset(
    {
        ("session_opened", "1.2.0"),
        ("session_resumed", "1.2.0"),
        ("plan_published", "1.1.0"),
        ("plan_revised", "1.1.0"),
    }
)


def may_carry_task_statement(record: LedgerRecord) -> bool:
    """Whether ``record``'s schema version can carry a task statement.

    Decided by the envelope alone, never the payload: redacting an event later must not make a
    review that held the statement look like one that predates the feature (issue #908).
    """

    return (
        type(record) is AcceptedEvent
        and (record.schema.name, record.schema.version) in _STATEMENT_CAPABLE_SCHEMAS
    )


def recorded_task_title(records: Iterable[LedgerRecord]) -> str | None:
    """Return the task title from the task's readable ``session_opened`` event, if any."""

    for record in records:
        if (
            type(record) is AcceptedEvent
            and record.redaction is RedactionState.PRESENT
            and type(record.payload) is SessionOpenedPayload
        ):
            return record.payload.task_title
    return None

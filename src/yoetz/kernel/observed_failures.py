"""One shared reading of hook-observed failed runs at a completion claim (#909).

A completion claim must disclose failed work that is still *live* when it is made. For a result
the harness observed (a hook-observed tool call), two later observed facts make an earlier
failure history instead of an omission:

* **Supersession.** A later hook-observed run of the *same command identity* succeeded, or ran
  again with any other outcome: only the latest run of a command identity is judged. The
  identity is the installation-keyed ``hmac-sha256:`` command commitment the hook computes and
  materialization stores as ``omitted:<commitment>`` in the action's ``command`` field; the raw
  command text never reaches the ledger. Any other command, and any unkeyed or structural
  placeholder, supersedes nothing.
* **State scope.** A later hook-observed workspace edit completed (an edit action whose result did
  not report failure). The failure described a workspace state that no longer exists. Legacy rows
  without a command identity (``omitted:structural``) reach this rule and never fall back to
  "every failure is live".

Only service-stamped hook observations take part on either side: a cooperative result keeps its
exact ADR-025 disclosure duty, and a cooperative "success" or "edit" can never retire a hook
failure. A superseded or historical failure is not deleted or hidden: the receipt counts it once
as history, and naming it in ``limitation_refs`` stays accepted.

The work-integrity and research-evidence packs, the claim-revision replay invariant, the receipt
builder and the observation-advice rule all read this module, so no two of them can disagree
about which observed failure is still live.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from yoetz.domain.events import (
    AcceptedEvent,
    ActionKind,
    ActionRecordedPayload,
    LedgerRecord,
    ResultOutcome,
    ResultRecordedPayload,
    is_observation_authored,
)
from yoetz.domain.values import ActionId, EventId, ResultId, event_id
from yoetz.kernel.projections import ProjectionRecord, ProjectionState
from yoetz.protocol.coverage import Coverage, PublicationChannel

__all__ = [
    "COMMAND_IDENTITY_PREFIX",
    "ObservedFailureState",
    "ObservedRun",
    "ObservedRunFacts",
    "classify_observed_runs",
    "command_identity",
    "is_observed_run_record",
    "observed_event_ids_from_coverage",
    "observed_event_ids_from_records",
    "observed_failure_states",
    "observed_failure_states_from_records",
    "observed_action_description",
    "observed_action_tool",
    "observed_run_facts",
]

# Hook materialization writes a command action's ``command`` as ``omitted:<digest>``; only an
# installation-keyed commitment is an identity. ``omitted:structural`` carries none.
COMMAND_IDENTITY_PREFIX: Final = "omitted:"
_COMMITMENT_PATTERN: Final = re.compile(r"\Ahmac-sha256:[0-9a-f]{64}\Z", re.ASCII)
_FAILED_OUTCOMES: Final = frozenset({ResultOutcome.FAILURE, ResultOutcome.PARTIAL})
_OBSERVED_RUN_FAMILIES: Final = frozenset({"action_recorded", "result_recorded"})
# Hook materialization appends the host tool name to an observed action's description in this
# closed form; the results view reads it back only from service-stamped observed actions.
_TOOL_SUFFIX_RE: Final = re.compile(r" \(tool ([A-Za-z0-9][A-Za-z0-9._:/+-]{0,127})\)\Z", re.ASCII)
_TOOL_TOKEN_RE: Final = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z", re.ASCII)


class ObservedFailureState(str, Enum):  # noqa: UP042 - stable closed token
    """Whether one hook-observed failure still needs disclosure at a point in the ledger."""

    LIVE = "live"
    SUPERSEDED = "superseded"
    RERUN = "rerun"
    HISTORICAL = "historical"


@dataclass(frozen=True, slots=True)
class ObservedRun:
    """One hook-observed tool result, reduced to the facts the supersession rule reads.

    ``position`` orders runs (ledger ingestion sequence, or envelope order for advice). ``edit``
    marks an edit tool call; it counts as a workspace change only when its own outcome is not a
    failure, because a failed or denied edit changed nothing.
    """

    ref: str
    position: int
    outcome: ResultOutcome
    identity: str | None = None
    edit: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.ref) is not str
            or not self.ref
            or type(self.position) is not int
            or type(self.outcome) is not ResultOutcome
            or (self.identity is not None and _COMMITMENT_PATTERN.fullmatch(self.identity) is None)
            or type(self.edit) is not bool
        ):
            raise ValueError("observed_run_invalid")


def command_identity(command: object) -> str | None:
    """Return the keyed command commitment an observed action carries, or ``None``.

    Only ``omitted:hmac-sha256:<hex>`` is an identity. Plain ``sha256`` digests are
    dictionary-guessable for short commands and are never produced as identities, and
    ``omitted:structural`` (every row recorded before command commitments existed) has none.
    """

    if type(command) is not str or not command.startswith(COMMAND_IDENTITY_PREFIX):
        return None
    value = command.removeprefix(COMMAND_IDENTITY_PREFIX)
    return value if _COMMITMENT_PATTERN.fullmatch(value) is not None else None


def classify_observed_runs(runs: Iterable[ObservedRun]) -> Mapping[str, ObservedFailureState]:
    """Classify every failed or partial run against the runs that follow it.

    One backward pass. Only the latest run of a command identity can be live: a failure is
    ``SUPERSEDED`` when a later run with the same identity succeeded, else ``RERUN`` when a later
    run with the same identity has any other outcome (that later run is the one judged), else
    ``HISTORICAL`` when a later edit completed, else ``LIVE``. A later failure of the same
    identity is a new failure in its own right; it never revives an earlier superseded one. A run
    without an identity is retired only by a later edit. The caller bounds ``runs`` to what
    precedes the point being judged.
    """

    ordered = sorted(runs, key=lambda run: run.position, reverse=True)
    if len({run.position for run in ordered}) != len(ordered):
        raise ValueError("observed_run_invalid")
    edited_after = False
    passed_after: set[str] = set()
    ran_after: set[str] = set()
    states: dict[str, ObservedFailureState] = {}
    for run in ordered:
        if run.outcome in _FAILED_OUTCOMES:
            if run.identity is not None and run.identity in passed_after:
                states[run.ref] = ObservedFailureState.SUPERSEDED
            elif run.identity is not None and run.identity in ran_after:
                states[run.ref] = ObservedFailureState.RERUN
            elif edited_after:
                states[run.ref] = ObservedFailureState.HISTORICAL
            else:
                states[run.ref] = ObservedFailureState.LIVE
        if run.edit and run.outcome is not ResultOutcome.FAILURE:
            edited_after = True
        if run.identity is not None:
            ran_after.add(run.identity)
            if run.outcome is ResultOutcome.SUCCESS:
                passed_after.add(run.identity)
    return MappingProxyType(states)


def is_observed_run_record(record: LedgerRecord) -> bool:
    """A service-stamped hook-observed action or result record (ADR-022 decision 2)."""

    return (
        type(record) is AcceptedEvent
        and record.schema.name in _OBSERVED_RUN_FAMILIES
        and record.publication_channel is PublicationChannel.HOOK_OBSERVED
        and is_observation_authored(record)
    )


def observed_event_ids_from_records(records: Iterable[LedgerRecord]) -> frozenset[EventId]:
    """The hook-observed action and result events of one accepted prefix."""

    return frozenset(record.event_id for record in records if is_observed_run_record(record))


def observed_event_ids_from_coverage[Ref: str](
    coverage_by_ref: Mapping[Ref, Coverage],
) -> frozenset[EventId]:
    """Source events whose service-derived coverage names the hook-observed channel.

    ADR-022 decision 2 keeps ``hook_observed`` unselectable by cooperative requests, so this is
    the same provenance test the research-evidence outcome narrowing already applies.
    """

    return frozenset(
        event_id(ref)
        for ref, coverage in coverage_by_ref.items()
        if ref.startswith("evt_")
        and PublicationChannel.HOOK_OBSERVED in set(coverage.publication_channels)
    )


def _projection_runs(
    results: Mapping[ResultId, ProjectionRecord[ResultRecordedPayload]],
    actions: Mapping[ActionId, ProjectionRecord[ActionRecordedPayload]],
    observed: frozenset[EventId],
    through: int,
) -> tuple[ObservedRun, ...]:
    runs: list[ObservedRun] = []
    for result_ref, record in results.items():
        payload = record.payload
        if (
            payload is None
            or record.source_frontier > through
            or record.source_event_id not in observed
        ):
            continue
        identity: str | None = None
        edit = False
        action = actions.get(payload.action_id)
        if action is not None and action.payload is not None and action.source_event_id in observed:
            if action.payload.action_kind is ActionKind.COMMAND:
                identity = command_identity(action.payload.command)
            elif action.payload.action_kind is ActionKind.EDIT:
                edit = True
        runs.append(
            ObservedRun(
                ref=str(result_ref),
                position=record.source_frontier,
                outcome=payload.outcome,
                identity=identity,
                edit=edit,
            )
        )
    return tuple(runs)


def observed_failure_states_from_records(
    results: Mapping[ResultId, ProjectionRecord[ResultRecordedPayload]],
    actions: Mapping[ActionId, ProjectionRecord[ActionRecordedPayload]],
    observed_event_ids: frozenset[EventId],
    *,
    through: int,
) -> Mapping[ResultId, ObservedFailureState]:
    """Classify each hook-observed failed or partial result recorded no later than ``through``.

    ``observed_event_ids`` are the source events the caller established as service-stamped
    hook observations. A result absent from the returned mapping is not hook-observed and keeps
    its ordinary disclosure duty. The replay reducer calls this form directly with the maps it is
    folding, so admission and the policies read one rule.
    """

    if type(observed_event_ids) is not frozenset:
        raise ValueError("observed_run_invalid")
    if type(through) is not int or through < 0:
        raise ValueError("observed_run_invalid")
    states = classify_observed_runs(_projection_runs(results, actions, observed_event_ids, through))
    return MappingProxyType({ResultId(ref): state for ref, state in states.items()})


def observed_failure_states(
    projection: ProjectionState,
    observed_event_ids: frozenset[EventId],
    *,
    through: int,
) -> Mapping[ResultId, ObservedFailureState]:
    """Classify the hook-observed failures of one projection up to ``through``."""

    if type(projection) is not ProjectionState:
        raise ValueError("observed_run_invalid")
    return observed_failure_states_from_records(
        projection.results, projection.actions, observed_event_ids, through=through
    )


def observed_action_description(base: str, tool: str | None) -> str:
    """Append the structural host tool name to an observed action description, when known."""

    if tool is None or _TOOL_TOKEN_RE.fullmatch(tool) is None:
        return base
    return f"{base} (tool {tool})"


def observed_action_tool(description: str) -> str | None:
    """Read back the tool name ``observed_action_description`` wrote, or ``None``."""

    match = _TOOL_SUFFIX_RE.search(description)
    return None if match is None else match.group(1)


@dataclass(frozen=True, slots=True)
class ObservedRunFacts:
    """What the results view shows so an agent can recognize its own observed run.

    Structural only: the 1-based occurrence among hook-observed results in ledger order, the host
    tool name, the keyed command commitment, and the recorded exit status. Never command text.
    The runner class (test, typecheck, build, lint, vcs, other) is #910's to add here.
    """

    occurrence: int
    tool_name: str | None
    command_commitment: str | None
    exit_status: int | None


def observed_run_facts(
    projection: ProjectionState,
    observed_event_ids: frozenset[EventId],
) -> Mapping[ResultId, ObservedRunFacts]:
    """Index every readable hook-observed result by its structural run facts."""

    if type(projection) is not ProjectionState or type(observed_event_ids) is not frozenset:
        raise ValueError("observed_run_invalid")
    ordered = sorted(
        (
            (record.source_frontier, result_ref, record)
            for result_ref, record in projection.results.items()
            if record.payload is not None and record.source_event_id in observed_event_ids
        ),
        key=lambda item: item[0],
    )
    facts: dict[ResultId, ObservedRunFacts] = {}
    for occurrence, (_frontier, result_ref, record) in enumerate(ordered, 1):
        payload = record.payload
        assert payload is not None
        tool: str | None = None
        commitment: str | None = None
        action = projection.actions.get(payload.action_id)
        if (
            action is not None
            and action.payload is not None
            and action.source_event_id in (observed_event_ids)
        ):
            tool = observed_action_tool(action.payload.description)
            if action.payload.action_kind is ActionKind.COMMAND:
                commitment = command_identity(action.payload.command)
        facts[result_ref] = ObservedRunFacts(occurrence, tool, commitment, payload.exit_status)
    return MappingProxyType(facts)

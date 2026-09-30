"""What an ``insufficient_packet`` review named as missing, fenced and classified (issue #907).

The reviewer may name only refs its packet carried. Yoetz, not the reviewer, decides whether the
agent can supply each item: a kind the effective review selection or channel can never carry, or a
target the owner redacted, is ``structurally_unavailable_on_this_host``; everything else is
``agent_suppliable``. A later review sees the earlier request beside what was recorded since, and
an item re-requested after it was supplied is dropped unless the reviewer cites that new material.
"Supplied" is judged per named target: only new material directly tied to that target counts
(its run, exact command, file path, or a correction of it), so a record that relates only to
another path, run or claim never answers it.

Pure: reads only the frozen projection and the frozen review selection.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol, cast

from yoetz.domain.events import (
    ActionRecordedPayload,
    ClaimRecordedPayload,
    ClaimRecordedPayloadV1_1,
    EvidenceDigestProvenance,
    EvidenceRecordedPayload,
    MissingForAssessmentItem,
    PlanPublishedPayload,
    PlanRevisedPayload,
    ResponseRecordedPayload,
    ResultRecordedPayload,
)
from yoetz.domain.findings import Finding
from yoetz.domain.privacy import ReviewSelectionPolicy
from yoetz.domain.receipts import (
    SEMANTIC_MISSING_AGENT_SUPPLIABLE_GAP,
    SEMANTIC_MISSING_ALREADY_SUPPLIED_GAP,
    SEMANTIC_MISSING_ITEMS_REJECTED_GAP,
    SEMANTIC_MISSING_UNAVAILABLE_GAP,
)
from yoetz.domain.values import EventId
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.kernel.projections import PendingMissingForAssessment, ProjectionState
from yoetz.ports.semantic import SemanticJudgment
from yoetz.protocol.models import MISSING_FOR_ASSESSMENT_KINDS, DataCategory

__all__ = [
    "MissingItemsReview",
    "review_missing_for_assessment",
    "supplied_since",
    "unsuppliable_missing_kinds",
]

AGENT_SUPPLIABLE: Final = "agent_suppliable"
STRUCTURALLY_UNAVAILABLE: Final = "structurally_unavailable_on_this_host"
# How many refs recorded since the request the next packet names per item, newest first.
_MAX_SUPPLIED_REFS: Final = 4
# Which recorded families can answer each missing kind. ``output_results`` are results that
# carry output (linked evidence or a summary): a bare outcome row names a run but not what it
# printed, so it can never answer a request for verification output.
_ANSWERING_FAMILIES: Final[Mapping[str, tuple[str, ...]]] = {
    "command_identity": ("actions", "results", "evidence"),
    "current_diff_for_path": ("evidence",),
    "other": ("evidence", "results"),
    "plan_or_claim_text": ("plans", "claims"),
    "prior_finding_context": ("responses",),
    "task_statement": ("plans",),
    "verification_output": ("evidence", "output_results"),
}
_FAMILY_NAMES: Final = (
    "actions",
    "results",
    "output_results",
    "evidence",
    "claims",
    "plans",
    "responses",
)


@dataclass(frozen=True, slots=True)
class MissingItemsReview:
    """The fenced, classified items a check records, and the coverage gaps they imply."""

    items: tuple[MissingForAssessmentItem, ...]
    gaps: frozenset[str]


def unsuppliable_missing_kinds(
    selection: ReviewSelectionPolicy, withheld_categories: Iterable[str]
) -> tuple[str, ...]:
    """Kinds the effective selection and channel can never carry, whatever the agent records.

    Publishing more material does not help when the approved review selection drops its section
    or excerpt kind, or the inference channel withholds its category; that is a limitation of this
    host's configuration, not work the agent can do.
    """

    withheld = frozenset(withheld_categories)
    sections = frozenset(selection.sections)
    kinds = frozenset(selection.excerpt_kinds)
    excerpts = (
        "targeted_excerpts" in sections
        and selection.max_excerpts > 0
        and DataCategory.EVIDENCE_EXCERPT.value not in withheld
    )
    goal = "goal" in sections and DataCategory.TASK_DESCRIPTION.value not in withheld
    claims = "claims" in sections and DataCategory.CLAIM_TEXT.value not in withheld
    carried = {
        "command_identity": (
            "targeted_excerpts" in sections
            and selection.max_excerpts > 0
            and selection.include_exact_command_text
            and "command" in kinds
            and DataCategory.COMMAND_METADATA.value not in withheld
        ),
        "current_diff_for_path": excerpts and bool({"diff", "evidence"} & kinds),
        "other": excerpts,
        "plan_or_claim_text": goal or claims,
        "prior_finding_context": (
            "timeline" in sections and DataCategory.FINDING_SUMMARY.value not in withheld
        ),
        "task_statement": goal,
        "verification_output": excerpts
        and bool({"command", "evidence", "failure", "test"} & kinds),
    }
    if frozenset(carried) != MISSING_FOR_ASSESSMENT_KINDS:
        raise RuntimeError("missing_for_assessment_kinds_incomplete")
    return tuple(sorted((kind for kind, ok in carried.items() if not ok), key=str.encode))


def supplied_since(
    projection: ProjectionState,
    pending: PendingMissingForAssessment,
    allowed: frozenset[str],
    observation_event_ids: frozenset[str] = frozenset(),
    captured_edit_paths: Mapping[str, frozenset[str]] | None = None,
) -> tuple[tuple[str, ...], ...]:
    """For each pending item, the case refs of answering material recorded after the request.

    A targeted item counts only new material directly tied to one of its targets (see ``_Links``;
    ``captured_edit_paths`` maps a hook-captured edit's evidence refs to the workspace-relative
    paths its authenticated bytes record, used only to compare); an item with no target counts
    any answering material of its kind. Yoetz cannot bind new material to reviewer prose, so the
    next reviewer reads these refs and decides whether they answer the request.
    """

    answered: list[tuple[str, ...]] = []
    for per_target in _answers_by_target(
        projection, pending, allowed, observation_event_ids, captured_edit_paths
    ):
        newest = sorted(
            {entry for entries in per_target.values() for entry in entries},
            key=lambda entry: (-entry[0], entry[1].encode("ascii")),
        )
        answered.append(
            tuple(sorted((ref for _order, ref in newest[:_MAX_SUPPLIED_REFS]), key=str.encode))
        )
    return tuple(answered)


def _answers_by_target(
    projection: ProjectionState,
    pending: PendingMissingForAssessment,
    allowed: frozenset[str],
    observation_event_ids: frozenset[str],
    captured_edit_paths: Mapping[str, frozenset[str]] | None = None,
) -> tuple[dict[str | None, tuple[tuple[int, str], ...]], ...]:
    """Per pending item and target, the answering material recorded since, as ``(order, ref)``.

    The key ``None`` holds an untargeted item's answers, matched by record family alone.
    """

    after = pending.source_frontier
    recorded: dict[str, list[tuple[int, str]]] = {family: [] for family in _FAMILY_NAMES}
    for family, rows in (
        ("actions", projection.actions),
        ("results", projection.results),
        ("evidence", projection.evidence),
        ("claims", projection.claims),
    ):
        for ref, row in rows.items():
            if row.source_frontier > after and row.payload is not None and not row.redacted:
                if str(row.source_event_id) in observation_event_ids or _hook_observed(row.payload):
                    # Hook capture records every tool call; it is never the agent answering a
                    # named request, so it must not turn a still-missing item into "supplied".
                    continue
                recorded[family].append((row.source_frontier, str(ref)))
                if type(row.payload) is ResultRecordedPayload and _carries_output(row.payload):
                    recorded["output_results"].append((row.source_frontier, str(ref)))
    for row in projection.plans.values():
        if row.source_frontier > after and row.payload is not None and not row.redacted:
            recorded["plans"].append((row.source_frontier, str(row.source_event_id)))
    for row in projection.responses.values():
        if row.source_frontier > after and row.payload is not None and not row.redacted:
            recorded["responses"].append((row.source_frontier, str(row.source_event_id)))
    links = _Links.of(projection, after, observation_event_ids, captured_edit_paths or {})
    answers: list[dict[str | None, tuple[tuple[int, str], ...]]] = []
    for item in pending.items:
        candidates = tuple(
            entry
            for family in _ANSWERING_FAMILIES[item.kind]
            for entry in recorded[family]
            if entry[1] in allowed
        )
        if not item.target_refs:
            answers.append({None: candidates})
            continue
        answers.append(
            {
                target: tuple(entry for entry in candidates if entry[1] in bound)
                for target in item.target_refs
                for bound in (links.answers(target),)
            }
        )
    return tuple(answers)


type _RunKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class _Subject:
    """What a target names: its runs (actions and exact-command keys) and its file paths."""

    actions: frozenset[str]
    keys: frozenset[_RunKey]
    paths: frozenset[str]

    def __or__(self, other: _Subject) -> _Subject:
        return _Subject(
            self.actions | other.actions, self.keys | other.keys, self.paths | other.paths
        )

    def __bool__(self) -> bool:
        return bool(self.actions or self.keys or self.paths)


_NO_SUBJECT: Final = _Subject(frozenset(), frozenset(), frozenset())


@dataclass(frozen=True, slots=True)
class _Links:
    """Direct relations from a named target to agent material recorded after the request.

    Only new, agent-published, unredacted material counts; citing the old target again is not
    new material, and nothing spreads through other newer rows or shared obligations. For an
    action, result or evidence target, the answer is: a result of the named action or of another
    run of the same exact command (compared with whitespace collapsed; a hook run is identified by
    its ``omitted:<digest>`` and matches only the same digest, never ``omitted:structural``), and
    the new evidence such a result cites; a ``git diff`` run naming one of the target's paths and
    the evidence its result cites; and new evidence whose ``reference`` is the target's own id or
    one of its paths. A target's paths are its evidence ``reference`` when that is a file path,
    the paths a hook-captured edit records (supplied by the caller from the authenticated
    capture), and the paths of a ``git diff`` command behind it. Paths compare normalized but
    exact (see ``_normal_path``). A claim is answered by material tied to what it cites; a
    correction (``claim_recorded`` 1.1.0 superseding it, or any claim disputing it) answers it,
    with the new material it cites when that is tied to the claim's support or the claim cited
    nothing Yoetz can relate. A finding is answered by a response to it and what that cites, a
    plan by the version that supersedes it, and an obligation by new actions, results, claims and
    plans that name it.
    """

    projection: ProjectionState
    after: int
    captured: Mapping[str, frozenset[str]]
    new_actions: Mapping[str, ActionRecordedPayload]
    new_results: Mapping[str, ResultRecordedPayload]
    new_evidence: Mapping[str, EvidenceRecordedPayload]
    new_claims: Mapping[str, ClaimRecordedPayload | ClaimRecordedPayloadV1_1]
    new_responses: Mapping[str, ResponseRecordedPayload]
    new_plans: Mapping[str, PlanPublishedPayload | PlanRevisedPayload]
    actions_by_key: Mapping[_RunKey, frozenset[str]]

    @classmethod
    def of(
        cls,
        projection: ProjectionState,
        after: int,
        observation_event_ids: frozenset[str],
        captured_edit_paths: Mapping[str, frozenset[str]],
    ) -> _Links:
        def newer(row: _Row) -> bool:
            return (
                row.source_frontier > after
                and row.payload is not None
                and not row.redacted
                and str(row.source_event_id) not in observation_event_ids
                and not _hook_observed(row.payload)
            )

        by_key: dict[_RunKey, set[str]] = {}
        for ref, row in projection.actions.items():
            key = _run_key(row.payload)
            if key is not None:
                by_key.setdefault(key, set()).add(str(ref))
        captured = {
            ref: frozenset(
                path for raw in paths if (path := _normal_path(raw, known_path=True)) is not None
            )
            for ref, paths in captured_edit_paths.items()
        }
        return cls(
            projection=projection,
            after=after,
            captured=captured,
            new_actions={
                str(ref): row.payload
                for ref, row in projection.actions.items()
                if newer(row) and row.payload is not None
            },
            new_results={
                str(ref): row.payload
                for ref, row in projection.results.items()
                if newer(row) and row.payload is not None
            },
            new_evidence={
                str(ref): row.payload
                for ref, row in projection.evidence.items()
                if newer(row) and row.payload is not None
            },
            new_claims={
                str(ref): row.payload
                for ref, row in projection.claims.items()
                if newer(row) and row.payload is not None
            },
            new_responses={
                str(row.source_event_id): row.payload
                for row in projection.responses.values()
                if newer(row) and row.payload is not None
            },
            new_plans={
                str(row.source_event_id): row.payload
                for row in projection.plans.values()
                if newer(row) and row.payload is not None
            },
            actions_by_key={key: frozenset(refs) for key, refs in by_key.items()},
        )

    def answers(self, target: str) -> frozenset[str]:
        """The new material directly tied to ``target``."""

        found: set[str] = {
            ref for ref, evidence in self.new_evidence.items() if evidence.reference == target
        }
        projection = self.projection
        claim = next((row for ref, row in projection.claims.items() if str(ref) == target), None)
        if claim is not None:
            return frozenset(found | self._claim_answers(target, claim.payload))
        if any(str(ref) == target for ref in projection.findings):
            for ref, response in self.new_responses.items():
                if str(response.finding_id) == target:
                    found.add(ref)
                    found.update(self._new_cited(response.evidence_refs))
            return frozenset(found)
        if any(str(ref) == target for ref in projection.obligations):
            return frozenset(found | self._obligation_answers(target))
        plan_version = next(
            (
                version
                for version, row in projection.plans.items()
                if str(row.source_event_id) == target
            ),
            None,
        )
        if plan_version is not None:
            found.update(
                ref
                for ref, plan in self.new_plans.items()
                if type(plan) is PlanRevisedPayload and plan.supersedes_plan_version == plan_version
            )
            return frozenset(found)
        return frozenset(found | self._material(self._subject(target)))

    def _new_cited(self, refs: Iterable[object]) -> set[str]:
        return {
            str(ref)
            for ref in refs
            if str(ref) in self.new_evidence or str(ref) in self.new_results
        }

    def _evidence_paths(self, ref: str) -> frozenset[str]:
        row = next(
            (item for key, item in self.projection.evidence.items() if str(key) == ref), None
        )
        paths = set(self.captured.get(ref, frozenset()))
        if row is not None and row.payload is not None and row.payload.reference is not None:
            path = _normal_path(row.payload.reference)
            if path is not None:
                paths.add(path)
        return frozenset(paths)

    def _action_subject(self, ref: str) -> _Subject:
        row = next((item for key, item in self.projection.actions.items() if str(key) == ref), None)
        payload = None if row is None else row.payload
        key = _run_key(payload)
        paths: frozenset[str] = frozenset() if payload is None else _diff_paths(payload.command)
        return _Subject(frozenset({ref}), frozenset() if key is None else frozenset({key}), paths)

    def _subject(self, target: str) -> _Subject:
        """The runs and paths an action, result or evidence target names (older rows only)."""

        projection = self.projection
        subject = _NO_SUBJECT
        if any(str(ref) == target for ref in projection.actions):
            subject = self._action_subject(target)
            for row in projection.results.values():
                if (
                    row.payload is not None
                    and row.source_frontier <= self.after
                    and str(row.payload.action_id) == target
                ):
                    for evidence_ref in row.payload.evidence_refs:
                        subject = subject | _Subject(
                            frozenset(), frozenset(), self._evidence_paths(str(evidence_ref))
                        )
            return subject
        result = next((row for ref, row in projection.results.items() if str(ref) == target), None)
        if result is not None:
            if result.payload is None:
                return subject
            subject = self._action_subject(str(result.payload.action_id))
            for evidence_ref in result.payload.evidence_refs:
                subject = subject | _Subject(
                    frozenset(), frozenset(), self._evidence_paths(str(evidence_ref))
                )
            return subject
        if any(str(ref) == target for ref in projection.evidence):
            subject = _Subject(frozenset(), frozenset(), self._evidence_paths(target))
            for row in projection.results.values():
                if (
                    row.payload is not None
                    and row.source_frontier <= self.after
                    and any(str(item) == target for item in row.payload.evidence_refs)
                ):
                    subject = subject | self._action_subject(str(row.payload.action_id))
        return subject

    def _material(self, subject: _Subject) -> set[str]:
        """New actions, results and evidence tied to a subject's runs or paths."""

        if not subject:
            return set()
        runs = set(subject.actions)
        for key in subject.keys:
            runs.update(self.actions_by_key.get(key, frozenset()))
        runs.update(
            ref
            for ref, action in self.new_actions.items()
            if _paths_meet(_diff_paths(action.command), subject.paths)
        )
        found = {ref for ref in self.new_actions if ref in runs}
        by_path = {
            ref
            for ref, evidence in self.new_evidence.items()
            if evidence.reference is not None
            and (path := _normal_path(evidence.reference)) is not None
            and _paths_meet(frozenset({path}), subject.paths)
        }
        found |= by_path
        for ref, result in self.new_results.items():
            cited = {str(item) for item in result.evidence_refs if str(item) in self.new_evidence}
            if str(result.action_id) in runs:
                found.add(ref)
                found |= cited
            elif cited & by_path:
                found.add(ref)
        return found

    def _claim_answers(
        self, target: str, claim: ClaimRecordedPayload | ClaimRecordedPayloadV1_1 | None
    ) -> set[str]:
        support: tuple[object, ...] = ()
        if claim is not None:
            support = (
                *claim.supporting_refs,
                *(claim.limitation_refs if type(claim) is ClaimRecordedPayloadV1_1 else ()),
            )
        subject = _NO_SUBJECT
        for ref in support:
            subject = subject | self._subject(str(ref))
        found = self._material(subject)
        for ref, correction in self.new_claims.items():
            replaced = (
                correction.supersedes_claim_refs
                if type(correction) is ClaimRecordedPayloadV1_1
                else ()
            )
            if not any(str(item) == target for item in (*replaced, *correction.disputes_refs)):
                continue
            found.add(ref)
            cited = self._new_cited(
                (
                    *correction.supporting_refs,
                    *(
                        correction.limitation_refs
                        if type(correction) is ClaimRecordedPayloadV1_1
                        else ()
                    ),
                )
            )
            # A claim that cited nothing Yoetz can relate is answered by what its correction
            # cites; otherwise only cited material tied to the claim's own support counts.
            found |= cited if not subject else cited & found
        return found

    def _obligation_answers(self, target: str) -> set[str]:
        found = {
            ref
            for ref, action in self.new_actions.items()
            if any(str(item) == target for item in action.obligation_refs)
        }
        for ref, result in self.new_results.items():
            if str(result.action_id) in found:
                found.add(ref)
                found |= {
                    str(item) for item in result.evidence_refs if str(item) in self.new_evidence
                }
        found |= {
            ref
            for ref, claim in self.new_claims.items()
            if any(str(item) == target for item in claim.obligation_refs)
        }
        for ref, plan in self.new_plans.items():
            named = (
                plan.obligation_refs
                if type(plan) is PlanPublishedPayload
                else tuple(
                    item
                    for change in cast(PlanRevisedPayload, plan).obligation_changes
                    for item in (change.obligation_id, *change.replacement_obligation_ids)
                )
            )
            if any(str(item) == target for item in named):
                found.add(ref)
        return found


class _Row(Protocol):
    @property
    def payload(self) -> object | None: ...
    @property
    def redacted(self) -> bool: ...
    @property
    def source_event_id(self) -> EventId: ...
    @property
    def source_frontier(self) -> int: ...


def _run_key(payload: ActionRecordedPayload | None) -> _RunKey | None:
    """What makes two runs the same command: its text with whitespace collapsed.

    A hook records ``omitted:<digest>`` instead of the text; the same digest is the same command
    text, so it matches only that digest. ``omitted:structural`` identifies nothing.
    """

    if payload is None or payload.command is None:
        return None
    command = " ".join(payload.command.split())
    if command.startswith("omitted:"):
        digest = command.removeprefix("omitted:")
        return None if digest in {"", "structural"} else ("digest", digest)
    return ("command", command) if command else None


def _normal_path(text: str, *, known_path: bool = False) -> str | None:
    """A file path, normalized but exact; ``None`` for anything that is not a path.

    Surrounding whitespace, ``./`` and ``.`` segments, repeated and trailing slashes are dropped;
    case and every other character are kept. A free-form evidence ``reference`` is a path only
    when it contains a slash or a file extension, so a generic reference such as ``stdout`` or a
    URL is never a path; ``known_path`` text (a captured edit's path, a ``git diff`` argument)
    needs neither.
    """

    text = text.strip()
    if not text or any(char.isspace() for char in text) or "://" in text:
        return None
    absolute = text.startswith("/")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or not (known_path or "/" in text or "." in parts[-1][1:]):
        return None
    return ("/" if absolute else "") + "/".join(parts)


def _paths_meet(left: frozenset[str], right: frozenset[str]) -> bool:
    """Whether two path sets share a path; an absolute path meets the relative path it ends with.

    Yoetz does not know the workspace root here, so ``/work/repo/src/a.py`` meets ``src/a.py`` at
    a path boundary and never ``a.py`` inside another name.
    """

    for one in left:
        for other in right:
            if one == other:
                return True
            if one.startswith("/") != other.startswith("/"):
                absolute, relative = (one, other) if one.startswith("/") else (other, one)
                if absolute.endswith("/" + relative):
                    return True
    return False


def _diff_paths(command: str | None) -> frozenset[str]:
    """The paths a ``git diff`` command names; empty for any other command."""

    if command is None:
        return frozenset()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return frozenset()
    paths: set[str] = set()
    for index in range(len(tokens) - 1):
        if tokens[index] != "git" or tokens[index + 1] != "diff":
            continue
        literal = False
        for token in tokens[index + 2 :]:
            if token in {"&&", "||", "|", ";"}:
                break
            if token == "--":
                literal = True
                continue
            if not literal and token.startswith("-"):
                continue
            path = _normal_path(token, known_path=True)
            if path is not None:
                paths.add(path)
    return frozenset(paths)


def _carries_output(payload: ResultRecordedPayload) -> bool:
    return bool(payload.evidence_refs) or bool(payload.summary and payload.summary.strip())


def _hook_observed(payload: object) -> bool:
    """Hook-captured bytes carry service-stamped observation provenance on their evidence row."""

    return (
        type(payload) is EvidenceRecordedPayload
        and payload.digest_binding is not None
        and payload.digest_binding.provenance is EvidenceDigestProvenance.OBSERVATION_CAPTURED
    )


def _redacted_refs(projection: ProjectionState) -> frozenset[str]:
    redacted: set[str] = set()
    for rows in (
        projection.obligations,
        projection.actions,
        projection.results,
        projection.evidence,
        projection.claims,
        projection.findings,
    ):
        for ref, row in rows.items():
            if row.redacted:
                redacted.update((str(ref), str(row.source_event_id)))
    return frozenset(redacted)


def review_missing_for_assessment(
    case: DeterministicCase,
    deterministic: tuple[Finding, ...],
    judgment: SemanticJudgment,
    *,
    unsuppliable_kinds: frozenset[str],
    citable_refs: frozenset[str] | None = None,
    captured_edit_paths: Mapping[str, frozenset[str]] | None = None,
) -> MissingItemsReview:
    """Fence what the reviewer named to the packet it was shown and classify who can supply it.

    A target outside the packet's ``citable_refs`` (the frozen case when the composing evaluator
    did not report the packet) is dropped (``semantic_missing_items_rejected``), as #905 trims a
    ruling's cited refs; an item whose every target was outside is dropped whole, because the
    reviewer named nothing the packet held.
    An ``insufficient_packet`` that named no item at all discloses the same gap.
    An item the prior review already requested and the agent answered since, target by target,
    is dropped unless the reviewer cites that newer material
    (``semantic_missing_already_supplied``), so the same request cannot loop; only new material
    directly tied to a target answers it (see ``_Links``; ``captured_edit_paths`` as in
    ``supplied_since``). Every kept item is recorded with Yoetz's own availability class.
    """

    if judgment.conclusion != "insufficient_packet":
        return MissingItemsReview((), frozenset())
    if not judgment.missing_for_assessment:
        # A reply in the 1.0.0 shape (a local model or prompt-only host) named nothing: the agent
        # cannot tell what to supply, so the check says so instead of reading as a named request.
        return MissingItemsReview((), frozenset({SEMANTIC_MISSING_ITEMS_REJECTED_GAP}))
    allowed = frozenset(str(ref) for ref in case.allowed_ids) | frozenset(
        str(item.finding_id) for item in deterministic
    )
    projection = case.projection
    pending = projection.pending_missing_for_assessment
    observed = frozenset(str(item) for item in case.observation_event_ids)
    answered = (
        ()
        if pending is None
        else _answers_by_target(projection, pending, allowed, observed, captured_edit_paths)
    )
    redacted = _redacted_refs(projection)
    shown = allowed if citable_refs is None else allowed & citable_refs
    gaps: set[str] = set()
    kept: dict[tuple[str, tuple[str, ...]], str] = {}
    for entry in judgment.missing_for_assessment:
        refs = tuple(ref for ref in entry.target_refs if ref in shown)
        if len(refs) != len(entry.target_refs):
            gaps.add(SEMANTIC_MISSING_ITEMS_REJECTED_GAP)
            if not refs:
                continue
        if pending is not None and any(
            prior.kind == entry.kind and _answered(prior, per_target, refs)
            for prior, per_target in zip(pending.items, answered, strict=True)
        ):
            gaps.add(SEMANTIC_MISSING_ALREADY_SUPPLIED_GAP)
            continue
        availability = (
            STRUCTURALLY_UNAVAILABLE
            if entry.kind in unsuppliable_kinds or set(refs) & redacted
            else AGENT_SUPPLIABLE
        )
        key = (entry.kind, refs)
        if kept.get(key) != STRUCTURALLY_UNAVAILABLE:
            kept[key] = availability
    items = tuple(
        MissingForAssessmentItem(kind=kind, target_refs=refs, availability=availability)
        for (kind, refs), availability in sorted(
            kept.items(),
            key=lambda pair: (
                pair[0][0].encode("ascii"),
                tuple(ref.encode("ascii") for ref in pair[0][1]),
            ),
        )
    )
    for item in items:
        gaps.add(
            SEMANTIC_MISSING_AGENT_SUPPLIABLE_GAP
            if item.availability == AGENT_SUPPLIABLE
            else SEMANTIC_MISSING_UNAVAILABLE_GAP
        )
    return MissingItemsReview(items, frozenset(gaps))


def _answered(
    prior: MissingForAssessmentItem,
    per_target: Mapping[str | None, tuple[tuple[int, str], ...]],
    refs: tuple[str, ...],
) -> bool:
    """Whether the prior request named every one of ``refs`` and each was answered since.

    An untargeted repeat of an untargeted request is answered by any material of its kind. A
    target the prior request did not name is a new request; a repeat that cites the newer
    material says it is still insufficient, so neither is dropped.
    """

    if not refs:
        return not prior.target_refs and bool(per_target.get(None))
    supplied = {ref for entries in per_target.values() for _order, ref in entries}
    return (
        set(refs) <= set(prior.target_refs)
        and all(per_target.get(ref) for ref in refs)
        and not supplied & set(refs)
    )

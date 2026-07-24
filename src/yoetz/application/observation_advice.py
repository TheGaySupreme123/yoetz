"""Build observation AdviceSnapshot from envelopes, optional inspect, and semantic add-ons."""

from __future__ import annotations

import hashlib
import inspect
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, cast

from yoetz.domain.findings import FindingId, finding_id
from yoetz.domain.observation import (
    AdviceItem,
    AdviceSnapshot,
    ObservationEnvelope,
    ObservationLifecycle,
    ObservationStatus,
    ObservationStatusQuery,
)
from yoetz.kernel.policies.observation_advice import (
    OBSERVATION_ADVICE_POLICY_ID,
    OBSERVATION_ADVICE_POLICY_VERSION,
    ObservationAdviceCandidate,
    ObservationAdviceContext,
    ObservationCheckFact,
    ObservationCompositionFact,
    ObservationInspectFact,
    evidence_basis_digest,
    observation_advice_findings,
)
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
)
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

__all__ = [
    "ObservationAdviceBuildInput",
    "ObservationAdviceContextBuilder",
    "ObservationAdviceSemanticAddon",
    "SemanticAdvicePort",
    "advice_items_for_ledger",
    "build_observation_advice_snapshot",
    "hook_advice_context",
    "minimized_semantic_evidence_packet",
    "should_reissue_advice",
    "stable_advice_finding_id",
]

_SUPPRESSION_DOMAIN: Final = b"yoetz/observation-advice-suppress/v1\x00"
_FINDING_DOMAIN: Final = b"yoetz/observation-advice-finding/v1\x00"

_RULE_SUMMARIES: Final[Mapping[str, str]] = {
    "failed_command_unresolved": "Unresolved failed command observed",
    "edit_after_successful_check": "Verification stale after later edit",
    "completion_without_verification": "Completion not supported by current evidence",
    "static_test_for_live_claim": "Static check does not support live claim",
    "subagent_finding_unaddressed": "Subagent finding remains unaddressed",
    "change_outside_plan": "Observed change outside declared plan scope",
    "observation_gap_or_stale": "Observation coverage is incomplete or stale",
    "provider_not_ready": "Configured provider is not ready",
    "semantic_claim_without_attempt": "Semantic claim lacks a recorded attempt",
}

_RULE_DETAILS: Final[Mapping[str, str]] = {
    "failed_command_unresolved": "A tool result failed and was not followed by a successful retry",
    "edit_after_successful_check": "A check that predates a later edit is no longer current verification",
    "completion_without_verification": "A completion claim lacks current admissible verification evidence",
    "static_test_for_live_claim": "Only static verification was observed for a live or wire claim",
    "subagent_finding_unaddressed": "A subagent reported a finding that parent work has not addressed",
    "change_outside_plan": "Changed-path evidence falls outside the declared plan digests",
    "observation_gap_or_stale": "Source lag, mapping, or drain gaps prevent complete observation",
    "provider_not_ready": "Semantic or provider binding is configured but not ready",
    "semantic_claim_without_attempt": "A semantic claim was observed without a matching attempt receipt",
}


@dataclass(frozen=True, slots=True)
class ObservationAdviceSemanticAddon:
    """Additive semantic advice identities already privacy-gated upstream."""

    finding_ids: tuple[FindingId, ...]
    evidence_digest: str | None
    next_action: str | None = None
    summaries: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    provider_identity: str | None = None
    attempt_receipt: str | None = None
    failure_reason: str | None = None


class SemanticAdvicePort(Protocol):
    """Optional semantic advisor; never required for deterministic correctness guidance."""

    def review(
        self,
        *,
        evidence_packet: Mapping[str, object],
    ) -> ObservationAdviceSemanticAddon | None: ...


class ObservationContextStore(Protocol):
    def list_envelopes(self, workspace: str) -> tuple[ObservationEnvelope, ...]: ...

    async def status(self, query: ObservationStatusQuery) -> ObservationStatus: ...

    def load_advice_snapshot(self, workspace: str) -> AdviceSnapshot | None: ...


type CallableFacts = Callable[[str], tuple[ObservationCheckFact, ...]]
type CallableInspect = Callable[[str], ObservationInspectFact | None]
type CallablePlans = Callable[[str], tuple[str, ...]]
type CallableSemantic = Callable[[str], ObservationAdviceSemanticAddon | None]
type CallableSemanticReview = Callable[
    [tuple[ObservationAdviceCandidate, ...], str, tuple[str, ...]],
    ObservationAdviceSemanticAddon | None | Awaitable[ObservationAdviceSemanticAddon | None],
]


@dataclass(frozen=True, slots=True)
class ObservationAdviceBuildInput:
    envelopes: tuple[ObservationEnvelope, ...]
    lifecycle: ObservationLifecycle
    gaps: tuple[str, ...]
    check_facts: tuple[ObservationCheckFact, ...] = ()
    inspect_fact: ObservationInspectFact | None = None
    composition: ObservationCompositionFact | None = None
    plan_path_digests: tuple[str, ...] = ()
    prior_snapshot: AdviceSnapshot | None = None
    semantic_addon: ObservationAdviceSemanticAddon | None = None
    has_real_observation: bool = False


@dataclass(frozen=True, slots=True)
class ObservationAdviceContextBuilder:
    """Load one coherent advice context from durable observation repositories.

    Optional verified facts are injected by their owning repositories; missing
    facts remain explicit coverage limitations rather than being invented from
    envelope shape.
    """

    check_facts: CallableFacts | None = None
    inspect_fact: CallableInspect | None = None
    composition: ObservationCompositionFact | None = None
    plan_path_digests: CallablePlans | None = None
    semantic_addon: CallableSemantic | None = None
    semantic_review: CallableSemanticReview | None = None

    async def build(
        self,
        workspace: str,
        store: ObservationContextStore,
        *,
        yoetz_session_id: str | None = None,
    ) -> AdviceSnapshot | None:
        envelopes = store.list_envelopes(workspace)
        status = await store.status(ObservationStatusQuery(workspace))
        store_check_facts = getattr(store, "load_check_facts", None)
        checks: tuple[ObservationCheckFact, ...] = ()
        if self.check_facts is not None:
            checks = self.check_facts(workspace)
        elif callable(store_check_facts):
            loaded = store_check_facts(workspace)
            if type(loaded) is tuple:
                loaded_items = cast(tuple[object, ...], loaded)
                if all(type(item) is ObservationCheckFact for item in loaded_items):
                    checks = cast(tuple[ObservationCheckFact, ...], loaded_items)
        inspect_fact = None if self.inspect_fact is None else self.inspect_fact(workspace)
        plans = () if self.plan_path_digests is None else self.plan_path_digests(workspace)
        semantic: ObservationAdviceSemanticAddon | None = None
        if self.semantic_review is not None:
            context = ObservationAdviceContext(
                envelopes=envelopes,
                lifecycle=status.lifecycle,
                gaps=status.gaps,
                check_facts=checks,
                inspect_fact=inspect_fact,
                composition=self.composition,
                plan_path_digests=plans,
            )
            candidates = observation_advice_findings(context)
            basis = evidence_basis_digest(
                candidates,
                envelopes,
                extra={
                    "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
                    "lifecycle": status.lifecycle.value,
                },
            )
            reviewed = self.semantic_review(candidates, basis, status.gaps)
            if inspect.isawaitable(reviewed):
                semantic = await reviewed
            else:
                semantic = reviewed
        elif self.semantic_addon is not None:
            semantic = self.semantic_addon(workspace)
        prior: AdviceSnapshot | None = None
        session_load = getattr(store, "load_advice_snapshot_for_session", None)
        if callable(session_load) and type(yoetz_session_id) is str:
            prior = session_load(workspace=workspace, yoetz_session_id=yoetz_session_id)
        if prior is None:
            prior = store.load_advice_snapshot(workspace)
        return build_observation_advice_snapshot(
            ObservationAdviceBuildInput(
                envelopes=envelopes,
                lifecycle=status.lifecycle,
                gaps=status.gaps,
                check_facts=checks,
                inspect_fact=inspect_fact,
                composition=self.composition,
                plan_path_digests=plans,
                prior_snapshot=prior,
                semantic_addon=semantic,
                has_real_observation=bool(envelopes),
            )
        )


def stable_advice_finding_id(rule_code: str, detail_token: str, evidence_digest: str) -> FindingId:
    """Allocate a deterministic UUIDv4-shaped finding id for observation advice."""

    material = _FINDING_DOMAIN + f"{rule_code}\0{detail_token}\0{evidence_digest}".encode()
    digest = hashlib.sha256(material).digest()
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return finding_id(PREFIX_BY_KIND[IdKind.FINDING] + str(uuid.UUID(bytes=bytes(raw))))


def _coverage(*, observation_qualified: bool, semantic: bool, gaps: Sequence[str]) -> Coverage:
    channels = (PublicationChannel.ENGINE_DERIVED,)
    authorship = AuthorshipAssurance.SERVICE_AUTHENTICATED
    observation = ArtifactObservation.PUBLISHED_ONLY
    if observation_qualified:
        channels = (PublicationChannel.HOOK_OBSERVED, PublicationChannel.ENGINE_DERIVED)
        authorship = AuthorshipAssurance.HARNESS_OBSERVED
        observation = ArtifactObservation.HOOK_OBSERVED
    checks = [CheckType.DETERMINISTIC]
    if semantic:
        checks.append(CheckType.SEMANTIC_MODEL_DERIVED)
    known = tuple(sorted({gap for gap in gaps if gap}, key=str.encode))
    freshness = LedgerFreshness.PARTIAL if known else LedgerFreshness.CURRENT
    return Coverage(
        publication_channels=tuple(sorted(channels, key=lambda item: item.value.encode("ascii"))),
        authorship_assurance=authorship,
        artifact_observation=observation,
        evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
        ledger_freshness=freshness,
        check_types=tuple(sorted(checks, key=lambda item: item.value.encode("ascii"))),
        known_gaps=known,
    )


def _suppression_identity(
    finding_ids: Sequence[FindingId],
    evidence_digest: str,
    next_action: str,
) -> str:
    material = _SUPPRESSION_DOMAIN + canonical_material(finding_ids, evidence_digest, next_action)
    digest = hashlib.sha256(material).hexdigest()
    return f"suppress-{digest[:48]}"


def canonical_material(
    finding_ids: Sequence[FindingId], evidence_digest: str, next_action: str
) -> bytes:
    joined = ",".join(str(item) for item in finding_ids)
    return f"{joined}\0{evidence_digest}\0{next_action}".encode()


def should_reissue_advice(
    prior: AdviceSnapshot | None,
    candidate: AdviceSnapshot,
    *,
    prior_severity: int | None = None,
    candidate_severity: int | None = None,
    unresolved_after_work: bool = False,
) -> bool:
    """Reissue when evidence changes, severity increases, or work left prior advice open."""

    if prior is None:
        return True
    if prior.suppression_identity == candidate.suppression_identity:
        return unresolved_after_work
    if prior.evidence_basis_digest != candidate.evidence_basis_digest:
        return True
    if (
        prior_severity is not None
        and candidate_severity is not None
        and candidate_severity < prior_severity
    ):
        # Lower priority number is higher severity in FindingKind traits.
        return True
    if unresolved_after_work:
        return True
    return prior.ranked_finding_ids != candidate.ranked_finding_ids


def _next_action(candidates: Sequence[ObservationAdviceCandidate]) -> str:
    if not candidates:
        return "reground_status"
    return candidates[0].next_action


def _freshness_frontier(envelopes: Sequence[ObservationEnvelope], evidence_digest: str) -> str:
    if not envelopes:
        return f"frontier-{evidence_digest.removeprefix('sha256:')[:24]}"
    last = envelopes[-1]
    return (
        f"frontier-g{last.cursor.source_generation}-"
        f"e{last.cursor.event_position}-"
        f"{evidence_digest.removeprefix('sha256:')[:16]}"
    )


def _item_from_candidate(
    candidate: ObservationAdviceCandidate,
    finding: FindingId,
    *,
    coverage: Coverage,
    freshness_frontier: str,
) -> AdviceItem:
    summary = _RULE_SUMMARIES.get(candidate.rule_code, "Observation advice finding")
    detail = _RULE_DETAILS.get(candidate.rule_code, "Evidence-linked observation finding")
    return AdviceItem(
        finding_id=finding,
        rule_code=candidate.rule_code,
        priority=candidate.priority,
        summary=summary,
        detail=detail,
        recommended_next_action=candidate.next_action,
        evidence_refs=candidate.evidence_refs,
        coverage=coverage,
        freshness_frontier=freshness_frontier,
        origin="deterministic",
    )


def build_observation_advice_snapshot(
    input_value: ObservationAdviceBuildInput,
) -> AdviceSnapshot | None:
    """Return an AdviceSnapshot, or None when there is nothing actionable to surface."""

    if type(input_value) is not ObservationAdviceBuildInput:
        raise ValueError("observation_advice_invalid")
    context = ObservationAdviceContext(
        envelopes=input_value.envelopes,
        lifecycle=input_value.lifecycle,
        gaps=input_value.gaps,
        check_facts=input_value.check_facts,
        inspect_fact=input_value.inspect_fact,
        composition=input_value.composition,
        plan_path_digests=input_value.plan_path_digests,
    )
    candidates = observation_advice_findings(context)
    basis = evidence_basis_digest(
        candidates,
        input_value.envelopes,
        extra={
            "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
            "lifecycle": input_value.lifecycle.value,
        },
    )
    finding_ids = tuple(
        stable_advice_finding_id(item.rule_code, item.detail_token, basis) for item in candidates
    )
    semantic = input_value.semantic_addon
    semantic_ids: tuple[FindingId, ...] = ()
    if semantic is not None and semantic.finding_ids:
        semantic_ids = semantic.finding_ids
        if semantic.evidence_digest is not None:
            basis = evidence_basis_digest(
                candidates,
                input_value.envelopes,
                extra={
                    "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
                    "lifecycle": input_value.lifecycle.value,
                    "semantic_evidence": semantic.evidence_digest,
                },
            )
    ranked = finding_ids + tuple(item for item in semantic_ids if item not in finding_ids)
    if not ranked:
        # Zero cooperative publications still yield observation-gap advice when empty/degraded.
        return None
    next_action = (
        semantic.next_action
        if semantic is not None and semantic.next_action is not None and not candidates
        else _next_action(candidates)
    )
    # next_action must be a token; semantic addon may supply one.
    if type(next_action) is not str or not next_action:
        next_action = "reground_status"
    coverage = _coverage(
        observation_qualified=input_value.has_real_observation
        and input_value.lifecycle is ObservationLifecycle.ACTIVE,
        semantic=semantic is not None and bool(semantic_ids),
        gaps=input_value.gaps,
    )
    # Honest observation-qualified coverage: when envelopes exist but lifecycle is not active,
    # keep engine-derived coverage and include known gaps.
    if input_value.envelopes and not (
        input_value.has_real_observation and input_value.lifecycle is ObservationLifecycle.ACTIVE
    ):
        gap_set = set(coverage.known_gaps)
        gap_set.add("observation_qualified_partial")
        coverage = Coverage(
            publication_channels=coverage.publication_channels,
            authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
            artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
            evidence_immutability=coverage.evidence_immutability,
            ledger_freshness=LedgerFreshness.PARTIAL,
            check_types=coverage.check_types,
            known_gaps=tuple(sorted(gap_set, key=str.encode)),
        )
    frontier = _freshness_frontier(input_value.envelopes, basis)
    items: list[AdviceItem] = [
        _item_from_candidate(candidate, finding, coverage=coverage, freshness_frontier=frontier)
        for candidate, finding in zip(candidates, finding_ids, strict=True)
    ]
    if semantic is not None and semantic_ids:
        for index, finding in enumerate(semantic_ids):
            if finding in finding_ids:
                continue
            summary = (
                semantic.summaries[index]
                if index < len(semantic.summaries)
                else "Model-derived observation note"
            )
            detail = (
                semantic.details[index]
                if index < len(semantic.details)
                else "Additive semantic advice over minimized evidence"
            )
            items.append(
                AdviceItem(
                    finding_id=finding,
                    rule_code="semantic-additive-review",
                    priority=90,
                    summary=summary[:160],
                    detail=detail[:240],
                    recommended_next_action=next_action,
                    evidence_refs=("semantic:minimized",),
                    coverage=coverage,
                    freshness_frontier=frontier,
                    origin="semantic_model_derived",
                )
            )
    suppression = _suppression_identity(ranked, basis, next_action)
    snapshot = AdviceSnapshot(
        ranked_finding_ids=ranked,
        evidence_basis_digest=basis,
        confidence_coverage=coverage,
        recommended_next_action=next_action,
        freshness_frontier=frontier,
        suppression_identity=suppression,
        ranked_items=tuple(items),
    )
    if not should_reissue_advice(input_value.prior_snapshot, snapshot):
        return input_value.prior_snapshot
    return snapshot


def hook_advice_context(snapshot: AdviceSnapshot) -> str:
    """Highest-priority summary, reason, next action, and one evidence reference."""

    if snapshot.ranked_items:
        top = snapshot.ranked_items[0]
        ref = top.evidence_refs[0] if top.evidence_refs else "evidence:none"
        text = (
            f"Yoetz: {top.summary}. Reason: {top.detail}. "
            f"Next: {top.recommended_next_action}. Evidence: {ref}."
        )
    else:
        findings = ",".join(str(item) for item in snapshot.ranked_finding_ids[:8])
        text = (
            f"Yoetz advice frontier {snapshot.freshness_frontier}: "
            f"next={snapshot.recommended_next_action}; findings={findings}."
        )
    return text[:512]


def advice_items_for_ledger(snapshot: AdviceSnapshot) -> tuple[AdviceItem, ...]:
    """Deterministic items for task-ledger materialization (Agent A coordinator hook)."""

    return tuple(item for item in snapshot.ranked_items if item.origin == "deterministic")


def minimized_semantic_evidence_packet(
    candidates: Sequence[ObservationAdviceCandidate],
    basis_digest: str,
    *,
    coverage_gaps: Sequence[str] = (),
    finding_summaries: Sequence[str] = (),
) -> dict[str, object]:
    """Build a minimized packet for optional semantic review (no repo/transcript/logs)."""

    return {
        "format": "yoetz.observation-advice-semantic/1",
        "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
        "evidence_basis_digest": basis_digest,
        "coverage_gaps": tuple(sorted({gap for gap in coverage_gaps if gap}, key=str.encode)),
        "finding_summaries": tuple(finding_summaries[:16]),
        "deterministic_rules": tuple(
            {
                "kind": item.kind.value,
                "rule_code": item.rule_code,
                "next_action": item.next_action,
                "evidence_ref_count": len(item.evidence_refs),
                "summary": _RULE_SUMMARIES.get(item.rule_code, "Observation advice finding"),
            }
            for item in candidates
        ),
    }

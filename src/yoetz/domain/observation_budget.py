"""Pure finite budgets and pressure selection for native observation.

Adapters pass an immutable usage sample and an explicit monotonic millisecond
sample to :func:`evaluate_pressure`; this module has no clock, storage, or
background state.  Limits are provisional candidates for issue #687:
performance validation has not been done.  Queue capacity and detail mode are
independent, and larger queue profiles never increase content-capture limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Final, Literal

__all__ = [
    "AdmissionDecision",
    "AdmissionReason",
    "AdmissionRequest",
    "BUDGET_POLICY_VERSION",
    "BUDGET_VALIDATION_STATUS",
    "BudgetLimits",
    "BudgetUsage",
    "CapacityProfile",
    "CURRENT_SERIALIZATION_CAP_BYTES",
    "ModeLimits",
    "ObservationMode",
    "PressureDimension",
    "PressureEvaluation",
    "PressureSnapshot",
    "PressureState",
    "PressureTransition",
    "evaluate_admission",
    "evaluate_pressure",
    "mode_limits",
]

BUDGET_POLICY_VERSION: Final = "observation-budget-v2-provisional"
BUDGET_VALIDATION_STATUS: Final = "not_validated"
CURRENT_SERIALIZATION_CAP_BYTES: Final = 1 * 1024 * 1024
RISING_WATERMARK_BPS: Final = 6_500
HIGH_WATERMARK_BPS: Final = 8_500
LOW_WATERMARK_BPS: Final = 4_500
RECOVERY_DWELL_MS: Final = 10_000
MAX_NOTICE_ID_BYTES: Final = 96


class ObservationMode(str, Enum):  # noqa: UP042 - stable domain value
    FOCUSED = "focused"
    DETAILED = "detailed"

    @classmethod
    def from_value(cls, value: object) -> ObservationMode:
        if isinstance(value, cls):
            return value
        if type(value) is not str:
            raise ValueError("observation_mode_invalid")
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError("observation_mode_invalid") from exc


class CapacityProfile(IntEnum):
    """The exact queue-count profiles available to an owner."""

    STANDARD = 512
    LARGER = 2_048
    LARGEST = 8_192

    @classmethod
    def from_value(cls, value: object) -> CapacityProfile:
        if isinstance(value, cls):
            return value
        if type(value) is str:
            try:
                value = int(value)
            except ValueError as exc:
                raise ValueError("capacity_profile_invalid") from exc
        if type(value) is not int:
            raise ValueError("capacity_profile_invalid")
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError("capacity_profile_invalid") from exc


class PressureState(str, Enum):  # noqa: UP042 - stable domain value
    HEALTHY = "healthy"
    RISING = "rising"
    HIGH = "high"
    HARD_LIMIT = "hard_limit"

    @property
    def rank(self) -> int:
        return _PRESSURE_RANK[self]


# Keep rank lookup outside the enum so the enum values remain the wire
# vocabulary and the ordering remains an implementation detail.
_PRESSURE_RANK: Final = {
    PressureState.HEALTHY: 0,
    PressureState.RISING: 1,
    PressureState.HIGH: 2,
    PressureState.HARD_LIMIT: 3,
}


class PressureDimension(str, Enum):  # noqa: UP042 - stable domain value
    COUNT = "count"
    BYTES = "bytes"
    OLDEST_AGE = "oldest_age"
    CAPTURE_BACKLOG = "capture_backlog"


class AdmissionReason(str, Enum):  # noqa: UP042 - stable domain value
    ACCEPTED = "accepted"
    HARD_LIMIT = "hard_limit"
    QUEUE_COUNT = "queue_count"
    QUEUE_BYTES = "queue_bytes"
    STATE_BYTES = "state_bytes"
    PENDING_ATTEMPTS = "pending_attempts"
    CAPTURE_TICKETS = "capture_tickets"
    CAPTURE_BYTES = "capture_bytes"
    PROTECTED_RESERVE = "protected_reserve"
    SESSION_FAIR_SHARE = "session_fair_share"


def _positive(value: object, field: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field}_invalid")


def _nonnegative(value: object, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field}_invalid")


def _ratio(value: int, limit: int) -> int:
    return value * 10_000 // limit


@dataclass(frozen=True, slots=True)
class ModeLimits:
    """Optional detail limits; these never alter privacy/category authority."""

    mode: ObservationMode
    max_records_per_summary: int
    max_optional_bytes: int
    max_input_bytes: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        if type(self.mode) is not ObservationMode:
            raise ValueError("mode_limits_mode_invalid")
        for value, field in (
            (self.max_records_per_summary, "max_records_per_summary"),
            (self.max_optional_bytes, "max_optional_bytes"),
            (self.max_input_bytes, "max_input_bytes"),
            (self.max_output_bytes, "max_output_bytes"),
        ):
            _positive(value, field)
        if self.max_optional_bytes > CURRENT_SERIALIZATION_CAP_BYTES:
            raise ValueError("mode_limits_exceeds_serialization_cap")


_FOCUSED_LIMITS: Final = ModeLimits(ObservationMode.FOCUSED, 16, 16 * 1024, 8 * 1024, 16 * 1024)
_DETAILED_LIMITS: Final = ModeLimits(ObservationMode.DETAILED, 16, 64 * 1024, 32 * 1024, 64 * 1024)


def mode_limits(mode: ObservationMode | str) -> ModeLimits:
    """Return the selected mode's finite optional-detail budget."""

    return (
        _FOCUSED_LIMITS
        if ObservationMode.from_value(mode) is ObservationMode.FOCUSED
        else _DETAILED_LIMITS
    )


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """All finite structural, pairing, capture, reserve, and fair-share limits."""

    profile: CapacityProfile
    queue_count: int
    queue_bytes: int
    state_bytes: int
    pending_attempts: int
    capture_tickets: int
    capture_bytes: int
    protected_count: int
    protected_bytes: int
    session_fair_share: int
    session_fair_share_bytes: int
    max_pending_age_ms: int = 60_000
    rising_watermark_bps: int = RISING_WATERMARK_BPS
    high_watermark_bps: int = HIGH_WATERMARK_BPS
    low_watermark_bps: int = LOW_WATERMARK_BPS
    recovery_dwell_ms: int = RECOVERY_DWELL_MS

    def __post_init__(self) -> None:
        if type(self.profile) is not CapacityProfile:
            raise ValueError("capacity_profile_invalid")
        for value, field in (
            (self.queue_count, "queue_count"),
            (self.queue_bytes, "queue_bytes"),
            (self.state_bytes, "state_bytes"),
            (self.pending_attempts, "pending_attempts"),
            (self.capture_tickets, "capture_tickets"),
            (self.capture_bytes, "capture_bytes"),
            (self.protected_count, "protected_count"),
            (self.protected_bytes, "protected_bytes"),
            (self.session_fair_share, "session_fair_share"),
            (self.session_fair_share_bytes, "session_fair_share_bytes"),
            (self.max_pending_age_ms, "max_pending_age_ms"),
            (self.recovery_dwell_ms, "recovery_dwell_ms"),
        ):
            _positive(value, field)
        for value, field in (
            (self.rising_watermark_bps, "rising_watermark_bps"),
            (self.high_watermark_bps, "high_watermark_bps"),
            (self.low_watermark_bps, "low_watermark_bps"),
        ):
            if type(value) is not int or not 0 < value < 10_000:
                raise ValueError(f"{field}_invalid")
        if not self.low_watermark_bps < self.rising_watermark_bps < self.high_watermark_bps:
            raise ValueError("pressure_watermarks_invalid")
        if self.queue_count != int(self.profile):
            raise ValueError("queue_count_profile_mismatch")
        if self.state_bytes < self.queue_bytes:
            raise ValueError("state_bytes_below_queue_bytes")
        if self.protected_count > self.queue_count or self.protected_bytes > self.queue_bytes:
            raise ValueError("protected_reserve_exceeds_queue")
        if (
            self.session_fair_share > self.queue_count
            or self.session_fair_share_bytes > self.queue_bytes
        ):
            raise ValueError("session_fair_share_exceeds_queue")

    @classmethod
    def for_profile(cls, profile: CapacityProfile | int | str) -> BudgetLimits:
        """Return provisional limits for exactly 512, 2,048, or 8,192 rows."""

        selected = CapacityProfile.from_value(profile)
        queue_bytes = {
            CapacityProfile.STANDARD: 512 * 1024,
            CapacityProfile.LARGER: 2 * 1024 * 1024,
            CapacityProfile.LARGEST: 8 * 1024 * 1024,
        }[selected]
        state_bytes = {
            CapacityProfile.STANDARD: CURRENT_SERIALIZATION_CAP_BYTES,
            CapacityProfile.LARGER: 4 * 1024 * 1024,
            CapacityProfile.LARGEST: 16 * 1024 * 1024,
        }[selected]
        count = int(selected)
        return cls(
            selected,
            count,
            queue_bytes,
            state_bytes,
            256,
            512,
            128 * 1024 * 1024,
            max(64, count // 4),
            max(128 * 1024, queue_bytes // 4),
            max(1, count // 4),
            max(1, queue_bytes // 4),
        )


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """One adapter-provided usage sample, including auxiliary state."""

    queue_count: int = 0
    queue_bytes: int = 0
    state_bytes: int = 0
    oldest_pending_age_ms: int = 0
    pending_attempts: int = 0
    capture_tickets: int = 0
    capture_bytes: int = 0
    protected_count: int = 0
    protected_bytes: int = 0
    session_queue_count: int = 0
    session_queue_bytes: int = 0

    def __post_init__(self) -> None:
        for field in (
            "queue_count",
            "queue_bytes",
            "state_bytes",
            "oldest_pending_age_ms",
            "pending_attempts",
            "capture_tickets",
            "capture_bytes",
            "protected_count",
            "protected_bytes",
            "session_queue_count",
            "session_queue_bytes",
        ):
            _nonnegative(getattr(self, field), field)
        if self.protected_count > self.queue_count:
            raise ValueError("protected_count_usage_exceeds_queue")
        if self.protected_bytes > self.queue_bytes:
            raise ValueError("protected_bytes_usage_exceeds_queue")

    def plus(self, request: AdmissionRequest) -> BudgetUsage:
        if type(request) is not AdmissionRequest:
            raise ValueError("admission_request_invalid")
        return BudgetUsage(
            queue_count=self.queue_count + request.queue_count,
            queue_bytes=self.queue_bytes + request.queue_bytes,
            state_bytes=self.state_bytes + request.state_bytes,
            oldest_pending_age_ms=self.oldest_pending_age_ms,
            pending_attempts=self.pending_attempts + request.pending_attempts,
            capture_tickets=self.capture_tickets + request.capture_tickets,
            capture_bytes=self.capture_bytes + request.capture_bytes,
            protected_count=self.protected_count
            + (request.queue_count if request.protected else 0),
            protected_bytes=self.protected_bytes
            + (request.queue_bytes if request.protected else 0),
            session_queue_count=self.session_queue_count + request.session_queue_count,
            session_queue_bytes=self.session_queue_bytes + request.session_queue_bytes,
        )


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """One structural admission delta before optional capture begins."""

    queue_count: int = 1
    queue_bytes: int = 0
    state_bytes: int = 0
    pending_attempts: int = 0
    capture_tickets: int = 0
    capture_bytes: int = 0
    protected: bool = False
    session_queue_count: int = 1
    session_queue_bytes: int = 0

    def __post_init__(self) -> None:
        for field in (
            "queue_count",
            "queue_bytes",
            "state_bytes",
            "pending_attempts",
            "capture_tickets",
            "capture_bytes",
            "session_queue_count",
            "session_queue_bytes",
        ):
            _nonnegative(getattr(self, field), field)
        if type(self.protected) is not bool:
            raise ValueError("protected_invalid")
        if self.queue_count == 0 and any(
            getattr(self, field)
            for field in (
                "queue_bytes",
                "state_bytes",
                "pending_attempts",
                "capture_tickets",
                "capture_bytes",
            )
        ):
            raise ValueError("admission_delta_without_record")


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    admitted: bool
    reason: AdmissionReason
    projected: BudgetUsage
    protected: bool


def evaluate_admission(
    usage: BudgetUsage,
    limits: BudgetLimits,
    request: AdmissionRequest,
) -> AdmissionDecision:
    """Evaluate finite limits, protected reserves, and session fairness."""

    if type(usage) is not BudgetUsage or type(limits) is not BudgetLimits:
        raise ValueError("budget_admission_input_invalid")
    if type(request) is not AdmissionRequest:
        raise ValueError("admission_request_invalid")
    projected = usage.plus(request)
    if max(value for _, value in _pressure_ratios(usage, limits)) >= 10_000:
        return AdmissionDecision(False, AdmissionReason.HARD_LIMIT, projected, request.protected)
    for failed, reason in (
        (projected.queue_count > limits.queue_count, AdmissionReason.QUEUE_COUNT),
        (projected.queue_bytes > limits.queue_bytes, AdmissionReason.QUEUE_BYTES),
        (projected.state_bytes > limits.state_bytes, AdmissionReason.STATE_BYTES),
        (projected.pending_attempts > limits.pending_attempts, AdmissionReason.PENDING_ATTEMPTS),
        (projected.capture_tickets > limits.capture_tickets, AdmissionReason.CAPTURE_TICKETS),
        (projected.capture_bytes > limits.capture_bytes, AdmissionReason.CAPTURE_BYTES),
    ):
        if failed:
            return AdmissionDecision(False, reason, projected, request.protected)
    if not request.protected:
        count_remaining = max(0, limits.protected_count - usage.protected_count)
        bytes_remaining = max(0, limits.protected_bytes - usage.protected_bytes)
        if projected.queue_count > limits.queue_count - count_remaining:
            return AdmissionDecision(False, AdmissionReason.PROTECTED_RESERVE, projected, False)
        if projected.queue_bytes > limits.queue_bytes - bytes_remaining:
            return AdmissionDecision(False, AdmissionReason.PROTECTED_RESERVE, projected, False)
    if (
        projected.session_queue_count > limits.session_fair_share
        or projected.session_queue_bytes > limits.session_fair_share_bytes
    ):
        return AdmissionDecision(
            False, AdmissionReason.SESSION_FAIR_SHARE, projected, request.protected
        )
    return AdmissionDecision(True, AdmissionReason.ACCEPTED, projected, request.protected)


@dataclass(frozen=True, slots=True)
class PressureSnapshot:
    state: PressureState
    since_ms: int
    low_since_ms: int | None = None
    transition_identity: str | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not PressureState:
            raise ValueError("pressure_state_invalid")
        _nonnegative(self.since_ms, "pressure_since_ms")
        if self.low_since_ms is not None:
            _nonnegative(self.low_since_ms, "pressure_low_since_ms")
            if self.low_since_ms < self.since_ms:
                raise ValueError("pressure_low_since_before_since")
        if self.transition_identity is not None and (
            type(self.transition_identity) is not str
            or not self.transition_identity.isascii()
            or not 1 <= len(self.transition_identity.encode("ascii")) <= MAX_NOTICE_ID_BYTES
        ):
            raise ValueError("pressure_transition_identity_invalid")


@dataclass(frozen=True, slots=True)
class PressureTransition:
    from_state: PressureState
    to_state: PressureState
    dimension: PressureDimension
    identity: str
    notice: Literal["downgrade", "recovery", "hard_limit", "pressure"]

    def __post_init__(self) -> None:
        if type(self.from_state) is not PressureState:
            raise ValueError("pressure_transition_from_invalid")
        if type(self.to_state) is not PressureState:
            raise ValueError("pressure_transition_to_invalid")
        if type(self.dimension) is not PressureDimension:
            raise ValueError("pressure_transition_dimension_invalid")
        if (
            type(self.identity) is not str
            or not self.identity.isascii()
            or not 1 <= len(self.identity.encode("ascii")) <= MAX_NOTICE_ID_BYTES
        ):
            raise ValueError("pressure_transition_identity_invalid")


@dataclass(frozen=True, slots=True)
class PressureEvaluation:
    selected_mode: ObservationMode
    effective_mode: ObservationMode
    content_allowed: bool
    admission_allowed: bool
    state: PressureState
    dimension: PressureDimension
    utilization_bps: int
    snapshot: PressureSnapshot
    transition: PressureTransition | None = None

    @property
    def transition_identity(self) -> str | None:
        return None if self.transition is None else self.transition.identity


def _pressure_ratios(
    usage: BudgetUsage,
    limits: BudgetLimits,
) -> tuple[tuple[PressureDimension, int], ...]:
    return (
        (
            PressureDimension.COUNT,
            max(
                _ratio(usage.queue_count, limits.queue_count),
                _ratio(usage.pending_attempts, limits.pending_attempts),
                _ratio(usage.session_queue_count, limits.session_fair_share),
            ),
        ),
        (
            PressureDimension.BYTES,
            max(
                _ratio(usage.queue_bytes, limits.queue_bytes),
                _ratio(usage.state_bytes, limits.state_bytes),
                _ratio(usage.session_queue_bytes, limits.session_fair_share_bytes),
            ),
        ),
        (
            PressureDimension.OLDEST_AGE,
            _ratio(usage.oldest_pending_age_ms, limits.max_pending_age_ms),
        ),
        (
            PressureDimension.CAPTURE_BACKLOG,
            max(
                _ratio(usage.capture_tickets, limits.capture_tickets),
                _ratio(usage.capture_bytes, limits.capture_bytes),
            ),
        ),
    )


def _candidate(
    ratios: tuple[tuple[PressureDimension, int], ...],
    limits: BudgetLimits,
) -> tuple[PressureState, PressureDimension, int]:
    dimension, value = max(ratios, key=lambda item: item[1])
    if value >= 10_000:
        state = PressureState.HARD_LIMIT
    elif value >= limits.high_watermark_bps:
        state = PressureState.HIGH
    elif value >= limits.rising_watermark_bps:
        state = PressureState.RISING
    else:
        state = PressureState.HEALTHY
    return state, dimension, value


def _all_low(
    ratios: tuple[tuple[PressureDimension, int], ...],
    limits: BudgetLimits,
) -> bool:
    return all(value <= limits.low_watermark_bps for _, value in ratios)


def _next_state(
    candidate: PressureState,
    previous: PressureSnapshot | None,
    *,
    low: bool,
    now_ms: int,
    dwell_ms: int,
) -> tuple[PressureState, int, int | None, bool]:
    if previous is None:
        return candidate, now_ms, None, False
    if candidate.rank > previous.state.rank:
        return candidate, now_ms, None, candidate is not previous.state
    if previous.state is PressureState.HARD_LIMIT and candidate is not PressureState.HARD_LIMIT:
        # Hard admission follows current usage. Optional detail still waits
        # for the low-water dwell, even when all pending work has drained.
        return PressureState.HIGH, now_ms, now_ms if low else None, True
    if candidate.rank == previous.state.rank:
        low_since = now_ms if low and previous.low_since_ms is None else previous.low_since_ms
        if not low:
            low_since = None
        if previous.state is not PressureState.HEALTHY and low_since is not None:
            if now_ms - low_since >= dwell_ms:
                return PressureState.HEALTHY, now_ms, None, True
        return previous.state, previous.since_ms, low_since, False
    if low:
        low_since = now_ms if previous.low_since_ms is None else previous.low_since_ms
        if now_ms - low_since >= dwell_ms:
            return PressureState.HEALTHY, now_ms, None, True
        return previous.state, previous.since_ms, low_since, False
    return previous.state, previous.since_ms, None, False


def evaluate_pressure(
    metrics: BudgetUsage,
    selected_mode: ObservationMode | str,
    previous: PressureSnapshot | PressureState | None = None,
    now_ms: int = 0,
    *,
    limits: BudgetLimits | None = None,
) -> PressureEvaluation:
    """Evaluate worst pressure and effective mode without sleeping or mutation."""

    if type(metrics) is not BudgetUsage:
        raise ValueError("pressure_metrics_invalid")
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("pressure_now_invalid")
    selected = ObservationMode.from_value(selected_mode)
    selected_limits = (
        BudgetLimits.for_profile(CapacityProfile.STANDARD) if limits is None else limits
    )
    if type(selected_limits) is not BudgetLimits:
        raise ValueError("pressure_limits_invalid")
    if previous is None:
        prior = None
    elif type(previous) is PressureSnapshot:
        prior = previous
    elif isinstance(previous, PressureState):
        prior = PressureSnapshot(previous, now_ms)
    else:
        raise ValueError("pressure_previous_invalid")
    ratios = _pressure_ratios(metrics, selected_limits)
    candidate, dimension, utilization = _candidate(ratios, selected_limits)
    if prior is None and candidate is not PressureState.HEALTHY:
        # A newly selected session can first appear under existing workspace
        # pressure. Report its initial reduction once, then retain the usual
        # snapshot identity so repeated inputs do not repeat the notice.
        prior = PressureSnapshot(PressureState.HEALTHY, now_ms)
    state, since_ms, low_since_ms, changed = _next_state(
        candidate,
        prior,
        low=_all_low(ratios, selected_limits),
        now_ms=now_ms,
        dwell_ms=selected_limits.recovery_dwell_ms,
    )
    effective = ObservationMode.FOCUSED if state.rank >= PressureState.HIGH.rank else selected
    identity = None if prior is None else prior.transition_identity
    transition: PressureTransition | None = None
    if changed and prior is not None and state is not prior.state:
        notice: Literal["downgrade", "recovery", "hard_limit", "pressure"]
        if state is PressureState.HEALTHY:
            notice = "recovery"
        elif state is PressureState.HARD_LIMIT:
            notice = "hard_limit"
        elif selected is ObservationMode.DETAILED and effective is ObservationMode.FOCUSED:
            notice = "downgrade"
        else:
            notice = "pressure"
        identity = (
            f"{BUDGET_POLICY_VERSION}:{prior.state.value}>{state.value}:{dimension.value}:{now_ms}"
        )
        if len(identity.encode("ascii")) > MAX_NOTICE_ID_BYTES:
            raise ValueError("pressure_transition_identity_invalid")
        transition = PressureTransition(prior.state, state, dimension, identity, notice)
    snapshot = PressureSnapshot(state, since_ms, low_since_ms, identity)
    return PressureEvaluation(
        selected_mode=selected,
        effective_mode=effective,
        # Rising pressure sheds optional retained content before it changes
        # the selected detail mode.  Protected structural evidence and
        # already-accepted backlog remain governed by their own lanes.
        content_allowed=state.rank < PressureState.RISING.rank,
        admission_allowed=state is not PressureState.HARD_LIMIT,
        state=state,
        dimension=dimension,
        utilization_bps=utilization,
        snapshot=snapshot,
        transition=transition,
    )

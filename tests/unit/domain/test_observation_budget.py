from __future__ import annotations

from dataclasses import replace

import pytest

from yoetz.domain.observation_budget import (
    BUDGET_VALIDATION_STATUS,
    CURRENT_SERIALIZATION_CAP_BYTES,
    AdmissionReason,
    AdmissionRequest,
    BudgetLimits,
    BudgetUsage,
    CapacityProfile,
    ObservationMode,
    PressureDimension,
    PressureState,
    evaluate_admission,
    evaluate_pressure,
    mode_limits,
)


@pytest.mark.parametrize(
    ("profile", "queue_bytes", "state_bytes"),
    [
        (CapacityProfile.STANDARD, 512 * 1024, 1 * 1024 * 1024),
        (CapacityProfile.LARGER, 2 * 1024 * 1024, 4 * 1024 * 1024),
        (CapacityProfile.LARGEST, 8 * 1024 * 1024, 16 * 1024 * 1024),
    ],
)
def test_profiles_are_exact_and_capture_limits_are_independent(
    profile: CapacityProfile,
    queue_bytes: int,
    state_bytes: int,
) -> None:
    limits = BudgetLimits.for_profile(profile)

    assert limits.queue_count == int(profile)
    assert limits.queue_bytes == queue_bytes
    assert limits.state_bytes == state_bytes
    assert limits.state_bytes >= CURRENT_SERIALIZATION_CAP_BYTES
    assert limits.pending_attempts == 256
    assert limits.capture_tickets == 512
    assert limits.capture_bytes == 128 * 1024 * 1024
    assert limits.protected_count > 0
    assert limits.protected_bytes > 0
    assert limits.session_fair_share > 0
    assert limits.session_fair_share_bytes > 0


def test_no_unvalidated_profile_is_constructible() -> None:
    with pytest.raises(ValueError, match="capacity_profile_invalid"):
        CapacityProfile.from_value(4_096)
    with pytest.raises(ValueError, match="capacity_profile_invalid"):
        BudgetLimits.for_profile("not-a-profile")


def test_mode_limits_are_separate_from_capacity_and_bounded_by_serialization() -> None:
    focused = mode_limits(ObservationMode.FOCUSED)
    detailed = mode_limits(ObservationMode.DETAILED)

    assert focused.mode is ObservationMode.FOCUSED
    assert detailed.mode is ObservationMode.DETAILED
    # Detail changes optional bytes; summaries use the same bounded shape
    # when pressure makes a Detailed selection effectively Focused.
    assert focused.max_records_per_summary == detailed.max_records_per_summary == 16
    assert focused.max_optional_bytes < detailed.max_optional_bytes
    assert detailed.max_optional_bytes < CURRENT_SERIALIZATION_CAP_BYTES
    assert mode_limits(ObservationMode.FOCUSED) == mode_limits(ObservationMode.FOCUSED)


def test_worst_pressure_dimension_is_count_bytes_age_or_capture_backlog() -> None:
    limits = BudgetLimits.for_profile(CapacityProfile.STANDARD)

    count = evaluate_pressure(
        BudgetUsage(queue_count=400),
        ObservationMode.DETAILED,
        limits=limits,
    )
    assert count.state is PressureState.RISING
    assert count.dimension is PressureDimension.COUNT
    assert count.effective_mode is ObservationMode.DETAILED
    assert count.content_allowed is False

    bytes_pressure = evaluate_pressure(
        BudgetUsage(state_bytes=900 * 1024),
        ObservationMode.DETAILED,
        limits=limits,
    )
    assert bytes_pressure.state is PressureState.HIGH
    assert bytes_pressure.dimension is PressureDimension.BYTES

    age = evaluate_pressure(
        BudgetUsage(oldest_pending_age_ms=55_000),
        ObservationMode.FOCUSED,
        limits=limits,
    )
    assert age.state is PressureState.HIGH
    assert age.dimension is PressureDimension.OLDEST_AGE

    capture = evaluate_pressure(
        BudgetUsage(capture_bytes=120 * 1024 * 1024),
        ObservationMode.FOCUSED,
        limits=limits,
    )
    assert capture.state is PressureState.HIGH
    assert capture.dimension is PressureDimension.CAPTURE_BACKLOG


def test_high_pressure_temporarily_demotes_detailed_and_hard_blocks_admission() -> None:
    limits = BudgetLimits.for_profile(CapacityProfile.STANDARD)
    high = evaluate_pressure(
        BudgetUsage(queue_bytes=450 * 1024),
        ObservationMode.DETAILED,
        limits=limits,
        now_ms=10,
    )
    assert high.state is PressureState.HIGH
    assert high.selected_mode is ObservationMode.DETAILED
    assert high.effective_mode is ObservationMode.FOCUSED
    assert high.content_allowed is False
    assert high.admission_allowed is True

    hard_metrics = BudgetUsage(oldest_pending_age_ms=limits.max_pending_age_ms)
    hard = evaluate_pressure(
        hard_metrics,
        ObservationMode.DETAILED,
        high.snapshot,
        20,
        limits=limits,
    )
    assert hard.state is PressureState.HARD_LIMIT
    assert hard.admission_allowed is False
    decision = evaluate_admission(
        hard_metrics,
        limits,
        AdmissionRequest(queue_count=1, session_queue_count=0),
    )
    assert decision.admitted is False
    assert decision.reason is AdmissionReason.HARD_LIMIT


def test_pressure_recovery_uses_low_dwell_and_emits_one_transition() -> None:
    limits = BudgetLimits.for_profile(CapacityProfile.STANDARD)
    high = evaluate_pressure(
        BudgetUsage(queue_count=450),
        ObservationMode.DETAILED,
        limits=limits,
        now_ms=1_000,
    )
    assert high.transition is not None
    assert high.transition.notice == "downgrade"

    same = evaluate_pressure(
        BudgetUsage(queue_count=450),
        ObservationMode.DETAILED,
        high.snapshot,
        2_000,
        limits=limits,
    )
    assert same.transition is None
    assert same.snapshot.transition_identity == high.snapshot.transition_identity

    low_before_dwell = evaluate_pressure(
        BudgetUsage(),
        ObservationMode.DETAILED,
        same.snapshot,
        3_000,
        limits=limits,
    )
    assert low_before_dwell.state is PressureState.HIGH
    assert low_before_dwell.effective_mode is ObservationMode.FOCUSED
    assert low_before_dwell.transition is None

    recovered = evaluate_pressure(
        BudgetUsage(),
        ObservationMode.DETAILED,
        low_before_dwell.snapshot,
        13_000,
        limits=limits,
    )
    assert recovered.state is PressureState.HEALTHY
    assert recovered.effective_mode is ObservationMode.DETAILED
    assert recovered.transition is not None
    assert recovered.transition.notice == "recovery"
    assert len(recovered.transition.identity.encode("ascii")) <= 96

    repeated = evaluate_pressure(
        BudgetUsage(),
        ObservationMode.DETAILED,
        recovered.snapshot,
        14_000,
        limits=limits,
    )
    assert repeated.transition is None
    assert repeated.snapshot.transition_identity == recovered.transition.identity


def test_pressure_downgrade_transition_identity_is_bounded_and_not_per_event() -> None:
    limits = BudgetLimits.for_profile(CapacityProfile.STANDARD)
    initial = evaluate_pressure(
        BudgetUsage(queue_count=450),
        ObservationMode.DETAILED,
        limits=limits,
        now_ms=1,
    )
    # Existing workspace pressure is visible on the session's first sample.
    assert initial.transition is not None
    assert initial.transition.notice == "downgrade"
    pressured = evaluate_pressure(
        BudgetUsage(queue_count=450),
        ObservationMode.DETAILED,
        initial.snapshot,
        2,
        limits=limits,
    )
    assert pressured.transition is None

    rising = evaluate_pressure(
        BudgetUsage(queue_count=500),
        ObservationMode.DETAILED,
        pressured.snapshot,
        3,
        limits=limits,
    )
    assert rising.state is PressureState.HIGH
    # The state did not change, so there is no event-sized notice.
    assert rising.transition is None

    degraded = evaluate_pressure(
        BudgetUsage(oldest_pending_age_ms=limits.max_pending_age_ms),
        ObservationMode.DETAILED,
        rising.snapshot,
        4,
        limits=limits,
    )
    assert degraded.state is PressureState.HARD_LIMIT
    assert degraded.transition is not None
    assert degraded.transition.notice == "hard_limit"
    assert degraded.transition.identity == degraded.snapshot.transition_identity


def test_optional_admission_stops_at_reserved_count_but_protected_can_use_target() -> None:
    limits = BudgetLimits.for_profile(CapacityProfile.STANDARD)
    optional_limit = limits.queue_count - limits.protected_count
    allowed = evaluate_admission(
        BudgetUsage(queue_count=optional_limit - 1),
        limits,
        AdmissionRequest(queue_count=1, session_queue_count=0),
    )
    assert allowed.admitted is True
    reserved = evaluate_admission(
        BudgetUsage(queue_count=optional_limit),
        limits,
        AdmissionRequest(queue_count=1, session_queue_count=0),
    )
    assert reserved.admitted is False
    assert reserved.reason is AdmissionReason.PROTECTED_RESERVE

    protected = evaluate_admission(
        BudgetUsage(queue_count=limits.queue_count - 1, protected_count=0),
        limits,
        AdmissionRequest(queue_count=1, protected=True, session_queue_count=0),
    )
    assert protected.admitted is True
    assert protected.projected.protected_count == 1


def test_capture_limits_remain_fixed_when_larger_profile_is_selected() -> None:
    limits = BudgetLimits.for_profile(CapacityProfile.LARGEST)
    decision = evaluate_admission(
        BudgetUsage(),
        limits,
        AdmissionRequest(
            queue_count=1,
            capture_tickets=limits.capture_tickets + 1,
            capture_bytes=1,
            session_queue_count=0,
        ),
    )
    assert decision.admitted is False
    assert decision.reason is AdmissionReason.CAPTURE_TICKETS


def test_provisional_values_advertise_missing_performance_validation() -> None:
    assert BUDGET_VALIDATION_STATUS == "not_validated"
    with pytest.raises(ValueError, match="pressure_watermarks_invalid"):
        replace(
            BudgetLimits.for_profile(CapacityProfile.STANDARD),
            low_watermark_bps=9_000,
        )


def test_new_detailed_session_under_pressure_gets_one_downgrade_notice() -> None:
    usage = BudgetUsage(capture_tickets=450)
    first = evaluate_pressure(usage, ObservationMode.DETAILED, now_ms=10)
    assert first.effective_mode is ObservationMode.FOCUSED
    assert first.transition is not None
    assert first.transition.notice == "downgrade"
    repeated = evaluate_pressure(usage, ObservationMode.DETAILED, first.snapshot, now_ms=20)
    assert repeated.transition is None


@pytest.mark.parametrize("mode", [ObservationMode.FOCUSED, ObservationMode.DETAILED])
def test_hard_pressure_reopens_admission_without_restoring_optional_detail(
    mode: ObservationMode,
) -> None:
    hard = evaluate_pressure(BudgetUsage(state_bytes=949_704, oldest_pending_age_ms=60_001), mode)
    drained = evaluate_pressure(BudgetUsage(state_bytes=949_704), mode, hard.snapshot, 100_000)
    assert drained.state is PressureState.HIGH
    assert drained.admission_allowed
    assert not drained.content_allowed
    tomorrow = evaluate_pressure(
        BudgetUsage(state_bytes=949_704), mode, drained.snapshot, 86_400_000
    )
    assert tomorrow.state is PressureState.HIGH
    assert tomorrow.admission_allowed
    assert not tomorrow.content_allowed
    low = evaluate_pressure(BudgetUsage(state_bytes=400_000), mode, tomorrow.snapshot, 86_400_001)
    assert not low.content_allowed
    recovered = evaluate_pressure(BudgetUsage(state_bytes=400_000), mode, low.snapshot, 86_410_001)
    assert recovered.state is PressureState.HEALTHY
    assert recovered.effective_mode is mode
    assert recovered.content_allowed

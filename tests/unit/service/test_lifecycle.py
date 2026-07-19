from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from yoetz.ports.control import ServiceState
from yoetz.ports.secret_memory import HumanAuthorizationProof
from yoetz.service.lifecycle import (
    IdleRelockPolicy,
    LifecycleError,
    ServiceLifecycle,
    SessionSecurityEvent,
)

_INSTANCE_ID = "svc_00000000-0000-4000-8000-000000000001"
_COMMITMENT = "sha256:" + "a" * 64


@dataclass(slots=True)
class _Clock:
    monotonic: float = 10.0

    def monotonic_seconds(self) -> float:
        return self.monotonic

    def now_utc(self) -> object:
        raise AssertionError("wall clock forbidden")


@dataclass(slots=True)
class _GenerationStore:
    generation: int = 0

    def advance(self, instance_id: str) -> int:
        assert instance_id == _INSTANCE_ID
        self.generation += 1
        return self.generation


def _lifecycle(clock: _Clock, **callbacks: object) -> ServiceLifecycle:
    return ServiceLifecycle(
        clock,  # pyright: ignore[reportArgumentType] - wall clock is intentionally unavailable
        generation_store=_GenerationStore(),
        process_start_identity_commitment=_COMMITMENT,
        instance_id=_INSTANCE_ID,
        **callbacks,  # pyright: ignore[reportArgumentType]
    )


@pytest.mark.anyio
async def test_ready_requires_positive_vault_generation_and_relock_clears_it() -> None:
    lifecycle = _lifecycle(_Clock())
    await lifecycle.acquire_singleton()
    with pytest.raises(LifecycleError, match="vault_locked"):
        await lifecycle.transition(ServiceState.READY)
    instance = await lifecycle.transition(ServiceState.READY, vault_generation=3)
    assert instance.state is ServiceState.READY
    assert lifecycle.current_vault_generation == 3
    await lifecycle.request_lock()
    assert lifecycle.state is ServiceState.LOCKED
    assert lifecycle.current_vault_generation is None


@pytest.mark.anyio
async def test_transition_table_rejects_every_unlisted_edge() -> None:
    lifecycle = _lifecycle(_Clock())
    await lifecycle.acquire_singleton()
    await lifecycle.transition(ServiceState.LOCKED)
    with pytest.raises(LifecycleError, match="invalid_transition"):
        await lifecycle.transition(ServiceState.READY, vault_generation=1)
    await lifecycle.transition(ServiceState.UNLOCKING)
    await lifecycle.transition(ServiceState.READY, vault_generation=1)
    with pytest.raises(LifecycleError, match="invalid_transition"):
        await lifecycle.transition(ServiceState.UNLOCKING)


@pytest.mark.anyio
async def test_admission_blocks_drain_until_released() -> None:
    closed = asyncio.Event()

    async def close_ready() -> None:
        closed.set()

    lifecycle = _lifecycle(_Clock(), close_ready_composition=close_ready)
    await lifecycle.acquire_singleton()
    await lifecycle.transition(ServiceState.READY, vault_generation=1)
    admission = await lifecycle.admit(
        "write",
        secret_use_class="secret_consumer",
        provider_call=True,
        shielded_commit=True,
    )
    draining = asyncio.create_task(lifecycle.request_lock())
    await asyncio.sleep(0)
    assert lifecycle.state is ServiceState.DRAINING
    assert not closed.is_set()
    with pytest.raises(LifecycleError, match="service_draining"):
        await lifecycle.admit("late")
    await lifecycle.release(admission)
    await draining
    assert closed.is_set()
    assert lifecycle.state is ServiceState.LOCKED


@pytest.mark.anyio
async def test_idle_policy_requires_exact_generation_bound_proof() -> None:
    clock = _Clock()
    lifecycle = _lifecycle(clock)
    await lifecycle.acquire_singleton()
    await lifecycle.transition(ServiceState.READY, vault_generation=9)
    proposed = IdleRelockPolicy(None)
    target = lifecycle.idle_relock_target_digest(lifecycle.idle_relock_policy, proposed)
    proof = HumanAuthorizationProof(
        "proof-idle",
        "idle_relock_policy_change",
        target,
        lifecycle.instance.generation,
        9,
        None,
        9.0,
        20.0,
    )
    assert await lifecycle.change_idle_relock_policy(proposed, proof) == proposed
    with pytest.raises(LifecycleError, match="human_authorization_stale"):
        await lifecycle.change_idle_relock_policy(IdleRelockPolicy(60), proof)


@pytest.mark.anyio
async def test_session_lock_and_monitor_loss_relock_but_wake_never_readies() -> None:
    lifecycle = _lifecycle(_Clock())
    await lifecycle.acquire_singleton()
    await lifecycle.transition(ServiceState.READY, vault_generation=1)
    await lifecycle.on_session_event(SessionSecurityEvent.USER_SESSION_LOCKED)
    assert lifecycle.state is ServiceState.LOCKED
    await lifecycle.on_session_event(SessionSecurityEvent.USER_SESSION_UNLOCKED)
    await lifecycle.on_session_event(SessionSecurityEvent.SYSTEM_RESUME)
    assert lifecycle.state is ServiceState.LOCKED


@pytest.mark.anyio
async def test_drain_deadline_fails_instead_of_claiming_locked() -> None:
    terminated = asyncio.Event()

    async def terminate() -> None:
        terminated.set()

    lifecycle = _lifecycle(_Clock(), terminate_on_deadline=terminate, lock_drain_seconds=0.01)
    await lifecycle.acquire_singleton()
    await lifecycle.transition(ServiceState.READY, vault_generation=1)
    await lifecycle.admit("stuck", secret_use_class="secret_consumer")
    with pytest.raises(LifecycleError, match="service_draining"):
        await lifecycle.request_lock()
    assert terminated.is_set()
    assert lifecycle.state is ServiceState.FAILED


def test_idle_policy_is_closed_and_restart_default_is_safe() -> None:
    assert IdleRelockPolicy().seconds == 900
    assert IdleRelockPolicy(None).canonical_value() == {"mode": "disabled"}
    with pytest.raises(ValueError, match="idle_relock_policy_invalid"):
        IdleRelockPolicy(59)

"""Per-user service lifecycle, admission fencing, draining, and idle relock."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import math
import os
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Final, Literal, Protocol, cast

from yoetz.domain.values import validate_sha256_digest
from yoetz.ports.clock import ClockPort
from yoetz.ports.control import ServiceState
from yoetz.ports.secret_memory import HumanAuthorizationProof, SecretMemoryError
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.ids import IdKind, new_id, validate_id

__all__ = [
    "LOCK_DRAIN_SECONDS",
    "STOP_DRAIN_SECONDS",
    "Admission",
    "IdleRelockPolicy",
    "LifecycleError",
    "ServiceInstance",
    "ServiceLifecycle",
    "SessionSecurityEvent",
]

LOCK_DRAIN_SECONDS: Final = 5
STOP_DRAIN_SECONDS: Final = 30
_DEFAULT_IDLE_SECONDS: Final = 900
_IDLE_POLICY_DOMAIN: Final = "yoetz/idle-relock-policy-change/v1\x00"
_LIFECYCLE_REASONS: Final = frozenset(
    {
        "service_already_running",
        "invalid_transition",
        "vault_locked",
        "service_draining",
        "session_monitor_unavailable",
        "human_authorization_required",
        "human_authorization_stale",
    }
)
_ALLOWED_TRANSITIONS: Final[dict[ServiceState, frozenset[ServiceState]]] = {
    ServiceState.STARTING: frozenset(
        {ServiceState.LOCKED, ServiceState.READY, ServiceState.FAILED}
    ),
    ServiceState.LOCKED: frozenset(
        {ServiceState.UNLOCKING, ServiceState.DRAINING, ServiceState.FAILED}
    ),
    ServiceState.UNLOCKING: frozenset(
        {ServiceState.READY, ServiceState.LOCKED, ServiceState.FAILED}
    ),
    ServiceState.READY: frozenset({ServiceState.DRAINING, ServiceState.FAILED}),
    ServiceState.DRAINING: frozenset({ServiceState.LOCKED, ServiceState.FAILED}),
    ServiceState.FAILED: frozenset({ServiceState.DRAINING}),
}


class LifecycleError(Exception):
    """Bounded lifecycle failure."""

    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or reason not in _LIFECYCLE_REASONS:
            raise TypeError("lifecycle_reason_invalid")
        self.reason = reason
        super().__init__(reason)


class SessionSecurityEvent(str, Enum):  # noqa: UP042 - frozen internal vocabulary
    USER_SESSION_LOCKED = "user_session_locked"
    SYSTEM_SUSPEND = "system_suspend"
    USER_SESSION_UNLOCKED = "user_session_unlocked"
    SYSTEM_RESUME = "system_resume"
    MONITOR_LOST = "monitor_lost"


@dataclass(frozen=True, slots=True)
class IdleRelockPolicy:
    seconds: int | None = _DEFAULT_IDLE_SECONDS

    def __post_init__(self) -> None:
        if self.seconds is not None and (
            type(self.seconds) is not int or not 60 <= self.seconds <= 86_400
        ):
            raise ValueError("idle_relock_policy_invalid")

    def canonical_value(self) -> dict[str, JsonValue]:
        if self.seconds is None:
            return {"mode": "disabled"}
        return {"mode": "finite", "seconds": self.seconds}


@dataclass(frozen=True, slots=True)
class ServiceInstance:
    instance_id: str
    generation: int
    process_start_identity_commitment: str
    state: ServiceState

    def __post_init__(self) -> None:
        validate_id(IdKind.SERVICE_INSTANCE, self.instance_id)
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("service_generation_invalid")
        validate_sha256_digest(self.process_start_identity_commitment)
        if type(self.state) is not ServiceState:
            raise ValueError("service_state_invalid")


type SecretUseClass = Literal["none", "secret_consumer"]
type CommitSectionState = Literal["none", "shielded"]


@dataclass(slots=True, repr=False)
class Admission:
    method: str
    secret_use_class: SecretUseClass
    commit_section_state: CommitSectionState
    provider_call: bool
    writer_queued: bool
    lease_held: bool
    _owner_token: object = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.method) is not str or not self.method:
            raise ValueError("admission_method_invalid")
        if self.secret_use_class not in {"none", "secret_consumer"}:
            raise ValueError("admission_secret_use_invalid")
        if self.commit_section_state not in {"none", "shielded"}:
            raise ValueError("admission_commit_state_invalid")
        if any(
            type(value) is not bool
            for value in (self.provider_call, self.writer_queued, self.lease_held)
        ):
            raise ValueError("admission_counter_invalid")

    def __repr__(self) -> str:
        return f"Admission(method={self.method!r}, released={self._released})"

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("admission_not_serializable")

    def belongs_to(self, owner_token: object) -> bool:
        return self._owner_token is owner_token and not self._released

    def mark_released(self) -> None:
        if self._released:
            raise LifecycleError("invalid_transition")
        self._released = True


class _GenerationStorePort(Protocol):
    def advance(self, instance_id: str) -> int: ...


class ServiceLifecycle:
    """Own exactly one service instance and all ready admission."""

    def __init__(
        self,
        clock: ClockPort,
        *,
        generation_store: _GenerationStorePort,
        process_start_identity_commitment: str,
        instance_id: str | None = None,
        singleton_lock_path: Path | None = None,
        endpoint_publisher: Callable[[ServiceInstance], Awaitable[None]] | None = None,
        endpoint_cleanup: Callable[[ServiceInstance], Awaitable[None]] | None = None,
        cancel_ready_work: Callable[[], Awaitable[None]] | None = None,
        close_ready_composition: Callable[[], Awaitable[None]] | None = None,
        terminate_on_deadline: Callable[[], Awaitable[None]] | None = None,
        lock_drain_seconds: float = float(LOCK_DRAIN_SECONDS),
        stop_drain_seconds: float = float(STOP_DRAIN_SECONDS),
    ) -> None:
        validate_sha256_digest(process_start_identity_commitment)
        if instance_id is not None:
            validate_id(IdKind.SERVICE_INSTANCE, instance_id)
        for value in (lock_drain_seconds, stop_drain_seconds):
            if type(value) is not float or not math.isfinite(value) or value <= 0.0:
                raise ValueError("drain_timeout_invalid")
        self._clock = clock
        self._generation_store = generation_store
        self._process_commitment = process_start_identity_commitment
        self._instance_id = instance_id or new_id(IdKind.SERVICE_INSTANCE)
        self._singleton_lock_path = singleton_lock_path
        self._endpoint_publisher = endpoint_publisher or _noop_instance
        self._endpoint_cleanup = endpoint_cleanup or _noop_instance
        self._cancel_ready_work = cancel_ready_work or _noop
        self._close_ready_composition = close_ready_composition or _noop
        self._terminate_on_deadline = terminate_on_deadline or _noop
        self._lock_drain_seconds = lock_drain_seconds
        self._stop_drain_seconds = stop_drain_seconds
        self._instance: ServiceInstance | None = None
        self._vault_generation: int | None = None
        self._policy = IdleRelockPolicy()
        self._last_quiescent_activity: float | None = None
        self._admissions: dict[int, Admission] = {}
        self._owner_token = object()
        self._mutex = asyncio.Lock()
        self._condition = asyncio.Condition(self._mutex)
        self._drain_task: asyncio.Task[None] | None = None
        self._idle_stop = asyncio.Event()
        self._singleton_fd: int | None = None
        self._endpoint_published = False
        self._closed = False

    @property
    def instance(self) -> ServiceInstance:
        instance = self._instance
        if instance is None:
            raise LifecycleError("invalid_transition")
        return instance

    @property
    def state(self) -> ServiceState:
        return self.instance.state

    @property
    def current_vault_generation(self) -> int | None:
        return self._vault_generation

    @property
    def idle_relock_policy(self) -> IdleRelockPolicy:
        return self._policy

    @staticmethod
    def generation_store(path: Path, installation_id: str) -> _GenerationStorePort:
        """Construct the private fixed-path durable generation source for composition."""

        return _ServiceGenerationStore(path, installation_id)

    async def acquire_singleton(self) -> ServiceInstance:
        async with self._mutex:
            if self._instance is not None:
                return self._instance
            if self._closed:
                raise LifecycleError("invalid_transition")
            path = self._singleton_lock_path
            if path is not None:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    os.close(descriptor)
                    raise LifecycleError("service_already_running") from exc
                self._singleton_fd = descriptor
            try:
                generation = self._generation_store.advance(self._instance_id)
            except Exception:
                self._release_singleton()
                raise
            self._instance = ServiceInstance(
                self._instance_id,
                generation,
                self._process_commitment,
                ServiceState.STARTING,
            )
            return self._instance

    async def publish_endpoint(self) -> None:
        async with self._mutex:
            if self._instance is None or self._endpoint_published or self._closed:
                raise LifecycleError("invalid_transition")
            instance = self._instance
            await self._endpoint_publisher(instance)
            self._endpoint_published = True

    async def transition(
        self,
        target: ServiceState,
        *,
        vault_generation: int | None = None,
    ) -> ServiceInstance:
        if type(target) is not ServiceState:
            raise LifecycleError("invalid_transition")
        async with self._mutex:
            current = self.instance.state
            if target not in _ALLOWED_TRANSITIONS[current]:
                raise LifecycleError("invalid_transition")
            if target is ServiceState.READY:
                if type(vault_generation) is not int or vault_generation <= 0:
                    raise LifecycleError("vault_locked")
                self._vault_generation = vault_generation
                now = self._sample_monotonic()
                self._last_quiescent_activity = now if not self._admissions else None
            else:
                if vault_generation is not None:
                    raise LifecycleError("invalid_transition")
                if current is ServiceState.READY or target in {
                    ServiceState.DRAINING,
                    ServiceState.LOCKED,
                    ServiceState.FAILED,
                }:
                    self._vault_generation = None
                    self._last_quiescent_activity = None
            self._instance = replace(self.instance, state=target)
            return self._instance

    async def admit(
        self,
        method: str,
        *,
        secret_use_class: SecretUseClass = "none",
        provider_call: bool = False,
        writer_queued: bool = False,
        lease_held: bool = False,
        shielded_commit: bool = False,
    ) -> Admission:
        async with self._condition:
            if self.state is ServiceState.DRAINING:
                raise LifecycleError("service_draining")
            if self.state is not ServiceState.READY:
                raise LifecycleError("vault_locked")
            admission = Admission(
                method,
                secret_use_class,
                "shielded" if shielded_commit else "none",
                provider_call,
                writer_queued,
                lease_held,
                self._owner_token,
            )
            self._admissions[id(admission)] = admission
            self._last_quiescent_activity = None
            return admission

    async def release(self, admission: Admission) -> None:
        async with self._condition:
            if (
                not admission.belongs_to(self._owner_token)
                or self._admissions.pop(id(admission), None) is not admission
            ):
                raise LifecycleError("invalid_transition")
            admission.mark_released()
            if not self._admissions and self.state is ServiceState.READY:
                self._last_quiescent_activity = self._sample_monotonic()
            self._condition.notify_all()

    async def note_activity(self) -> None:
        async with self._mutex:
            if self.state is not ServiceState.READY:
                return
            if not self._admissions:
                self._last_quiescent_activity = self._sample_monotonic()

    async def request_lock(self, reason: str = "explicit") -> None:
        del reason  # state publication owns bounded reasons outside this internal coordinator
        task = await self._coalesced_drain(stop=False)
        await task

    async def request_stop(self, reason: str = "explicit") -> None:
        del reason
        task = await self._coalesced_drain(stop=True)
        await task

    async def on_session_event(self, event: SessionSecurityEvent) -> None:
        if type(event) is not SessionSecurityEvent:
            raise LifecycleError("session_monitor_unavailable")
        if event in {
            SessionSecurityEvent.USER_SESSION_LOCKED,
            SessionSecurityEvent.SYSTEM_SUSPEND,
            SessionSecurityEvent.MONITOR_LOST,
        }:
            if self.state is ServiceState.READY:
                await self.request_lock(event.value)
            return
        # Unlock/resume are deliberately state preserving.

    async def run_idle_monitor(self, *, poll_seconds: float = 1.0) -> None:
        if type(poll_seconds) is not float or not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("idle_poll_invalid")
        while not self._idle_stop.is_set():
            await asyncio.sleep(poll_seconds)
            should_lock = False
            async with self._mutex:
                if self.state is not ServiceState.READY or self._admissions:
                    continue
                seconds = self._policy.seconds
                started = self._last_quiescent_activity
                if seconds is None or started is None:
                    continue
                should_lock = self._sample_monotonic() - started >= seconds
            if should_lock:
                await self.request_lock("idle_expired")

    async def change_idle_relock_policy(
        self,
        proposed: IdleRelockPolicy,
        proof: HumanAuthorizationProof,
    ) -> IdleRelockPolicy:
        if type(proposed) is not IdleRelockPolicy:
            raise LifecycleError("human_authorization_required")
        async with self._mutex:
            if self.state is not ServiceState.READY:
                raise LifecycleError("human_authorization_stale")
            vault_generation = self._vault_generation
            if vault_generation is None:
                raise LifecycleError("human_authorization_stale")
            target_digest = self.idle_relock_target_digest(self._policy, proposed)
            now = self._sample_monotonic()
            try:
                proof.consume(
                    "idle_relock_policy_change",
                    target_digest,
                    self.instance.generation,
                    vault_generation,
                    None,
                    now,
                )
            except SecretMemoryError as exc:
                raise LifecycleError("human_authorization_stale") from exc
            self._policy = proposed
            self._last_quiescent_activity = now if not self._admissions else None
            return self._policy

    def idle_relock_target_digest(
        self,
        current: IdleRelockPolicy,
        proposed: IdleRelockPolicy,
    ) -> str:
        value: dict[str, JsonValue] = {
            "current": current.canonical_value(),
            "proposed": proposed.canonical_value(),
            "service_generation": self.instance.generation,
        }
        return _domain_digest(_IDLE_POLICY_DOMAIN, cast(JsonValue, value))

    async def close(self) -> None:
        if self._closed:
            return
        if self._instance is not None and self.state not in {
            ServiceState.LOCKED,
            ServiceState.DRAINING,
            ServiceState.FAILED,
        }:
            await self.request_stop("close")
        async with self._mutex:
            if self._closed:
                return
            if self._endpoint_published:
                await self._endpoint_cleanup(self.instance)
                self._endpoint_published = False
            self._closed = True
            self._idle_stop.set()
            self._release_singleton()

    async def _coalesced_drain(self, *, stop: bool) -> asyncio.Task[None]:
        async with self._mutex:
            existing = self._drain_task
            if existing is not None and not existing.done():
                return existing
            state = self.state
            if not stop and state is ServiceState.LOCKED:
                return asyncio.create_task(_noop())
            if stop and state in {ServiceState.LOCKED, ServiceState.FAILED}:
                self._instance = replace(self.instance, state=ServiceState.DRAINING)
            elif state is ServiceState.READY:
                self._vault_generation = None
                self._last_quiescent_activity = None
                self._instance = replace(self.instance, state=ServiceState.DRAINING)
            elif state is not ServiceState.DRAINING:
                raise LifecycleError("invalid_transition")
            task = asyncio.create_task(self._drain(stop=stop))
            self._drain_task = task
            return task

    async def _drain(self, *, stop: bool) -> None:
        await self._cancel_ready_work()
        timeout = self._stop_drain_seconds if stop else self._lock_drain_seconds
        try:
            async with asyncio.timeout(timeout):
                async with self._condition:
                    await self._condition.wait_for(lambda: not self._admissions)
        except TimeoutError as exc:
            await self._terminate_on_deadline()
            async with self._mutex:
                self._instance = replace(self.instance, state=ServiceState.FAILED)
                self._vault_generation = None
            raise LifecycleError("service_draining") from exc
        await self._close_ready_composition()
        async with self._mutex:
            self._vault_generation = None
            self._last_quiescent_activity = None
            self._instance = replace(
                self.instance,
                state=ServiceState.DRAINING if stop else ServiceState.LOCKED,
            )
        if stop:
            async with self._mutex:
                if self._endpoint_published:
                    await self._endpoint_cleanup(self.instance)
                    self._endpoint_published = False

    def _sample_monotonic(self) -> float:
        value = self._clock.monotonic_seconds()
        if type(value) is not float or not math.isfinite(value) or value < 0.0:
            raise LifecycleError("invalid_transition")
        return value

    def _release_singleton(self) -> None:
        descriptor = self._singleton_fd
        self._singleton_fd = None
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


class _ServiceGenerationStore:
    """Owner-only canonical service generation metadata.

    The path is fixed by composition; this class never derives it from caller content.
    """

    def __init__(self, path: Path, installation_id: str) -> None:
        self._path = path
        self._installation_id = validate_id(IdKind.INSTALLATION, installation_id)

    def advance(self, instance_id: str) -> int:
        validate_id(IdKind.SERVICE_INSTANCE, instance_id)
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _verify_private_directory(self._path.parent)
        generation = 1
        if self._path.exists():
            value = self._read()
            generation = _required_generation(value["generation"]) + 1
        value: dict[str, JsonValue] = {
            "generation": str(generation),
            "installation_id": self._installation_id,
            "last_instance_id": instance_id,
            "schema_version": "1",
        }
        digest = _domain_digest("yoetz/service-generation/v1\x00", cast(JsonValue, value))
        value["record_digest"] = digest
        data = canonical_encode(value) + b"\n"
        temporary = self._path.with_name(f".{self._path.name}.{os.urandom(12).hex()}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self._path)
        directory = os.open(self._path.parent, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return generation

    def _read(self) -> dict[str, JsonValue]:
        metadata = self._path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise LifecycleError("invalid_transition")
        data = self._path.read_bytes()
        if not data.endswith(b"\n") or data.endswith(b"\n\n"):
            raise LifecycleError("invalid_transition")
        parsed = strict_json_parse(data[:-1])
        if type(parsed) is not dict:
            raise LifecycleError("invalid_transition")
        value = cast(dict[str, JsonValue], parsed)
        if set(value) != {
            "generation",
            "installation_id",
            "last_instance_id",
            "record_digest",
            "schema_version",
        }:
            raise LifecycleError("invalid_transition")
        if value["installation_id"] != self._installation_id or value["schema_version"] != "1":
            raise LifecycleError("invalid_transition")
        validate_id(IdKind.SERVICE_INSTANCE, value["last_instance_id"])
        record_digest = value["record_digest"]
        if type(record_digest) is not str:
            raise LifecycleError("invalid_transition")
        body = dict(value)
        del body["record_digest"]
        expected = _domain_digest("yoetz/service-generation/v1\x00", cast(JsonValue, body))
        if not hmac_compare(record_digest, expected):
            raise LifecycleError("invalid_transition")
        return value


def _required_generation(value: JsonValue) -> int:
    if type(value) is not str or not value.isascii() or not value.isdecimal():
        raise LifecycleError("invalid_transition")
    if value != str(int(value)) or int(value) <= 0:
        raise LifecycleError("invalid_transition")
    return int(value)


def _verify_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise LifecycleError("invalid_transition")


def hmac_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


def _domain_digest(domain: str, value: JsonValue) -> str:
    return f"sha256:{hashlib.sha256(domain.encode('ascii') + canonical_encode(value)).hexdigest()}"


async def _noop() -> None:
    return None


async def _noop_instance(_instance: ServiceInstance) -> None:
    return None

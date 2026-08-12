"""Durable, owner-controlled recommended-default advisories.

Recommendations are advice, not authority.  Evaluation may add a bounded pending id, but only
an explicit CLI acceptance is allowed to apply a change.  Declines are durable and suppress the
same recommendation permanently.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from collections.abc import Awaitable, Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, cast

from yoetz.application.package_update import (
    PackageUpdateAdvisory,
    installed_package_version,
    resolve_package_update_advisory,
)
from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir
from yoetz.domain.privacy import PrivacyPolicy

__all__ = [
    "RECOMMENDED_DEFAULTS",
    "RecommendationContext",
    "RecommendationDecision",
    "RecommendationState",
    "RecommendationStoreError",
    "RecommendedDefault",
    "cached_pending_recommendations",
    "evaluate_recommendation_context",
    "load_recommendation_state",
    "recommendation_by_id",
    "record_recommendation_decision",
    "refresh_pending",
    "store_recommendation_state",
]

type RecommendationKind = Literal["config_flip", "activation", "package_update"]
type RecommendationDecisionValue = Literal["accepted", "declined"]
type RecommendationContextFactory = Callable[[], Awaitable["RecommendationContext"]]

_SCHEMA: Final = "yoetz.recommendations/1"
_STORE_NAME: Final = "recommendations.json"
_LOCK_NAME: Final = "recommendations.lock"
_MAX_STORE_BYTES: Final = 32 * 1024
_MAX_DECISIONS: Final = 128
_MAX_TEXT_LENGTH: Final = 256
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$", re.ASCII)


class RecommendationStoreError(Exception):
    """A bounded failure reading or durably writing recommendation decisions."""

    reason_code: str

    def __init__(self, reason_code: str) -> None:
        if reason_code not in {
            "recommendation_store_corrupt",
            "recommendation_store_unsafe",
            "recommendation_store_write_failed",
        }:
            raise ValueError("recommendation_store_reason_invalid")
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class RecommendationContext:
    """Already-resolved facts used by the pure recommendation registry."""

    observation_enabled: bool | None = None
    codex_activation_state: str | None = None
    package_update: PackageUpdateAdvisory | None = None


@dataclass(frozen=True, slots=True)
class RecommendedDefault:
    id: str
    introduced_in: str
    title: str
    summary: str
    kind: RecommendationKind
    is_satisfied: Callable[[RecommendationContext], bool] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if _ID.fullmatch(self.id) is None:
            raise ValueError("recommendation_id_invalid")
        for value in (self.introduced_in, self.title, self.summary):
            if type(value) is not str or not value or len(value) > _MAX_TEXT_LENGTH:
                raise ValueError("recommendation_text_invalid")


@dataclass(frozen=True, slots=True)
class RecommendationDecision:
    decision: RecommendationDecisionValue
    decided_at: datetime
    version: str


@dataclass(frozen=True, slots=True)
class RecommendationState:
    schema: str = _SCHEMA
    last_evaluated_version: str | None = None
    decisions: Mapping[str, RecommendationDecision] = field(
        default_factory=lambda: MappingProxyType({})
    )
    pending: tuple[str, ...] = ()


def _observation_satisfied(context: RecommendationContext) -> bool:
    # Unknown consumers fail conservatively: absence of a bound config reader never creates
    # advice that cannot be justified from a current value.
    return context.observation_enabled is not False


def _activation_satisfied(context: RecommendationContext) -> bool:
    return context.codex_activation_state is None or context.codex_activation_state == "active"


def _package_update_satisfied(context: RecommendationContext) -> bool:
    advisory = context.package_update
    return advisory is None or not advisory.is_newer


RECOMMENDED_DEFAULTS: Final[tuple[RecommendedDefault, ...]] = (
    RecommendedDefault(
        id="observation-enabled",
        introduced_in="0.1.0",
        title="Enable local observation",
        summary="Enable the local observation pipeline; workspace consent remains required.",
        kind="config_flip",
        is_satisfied=_observation_satisfied,
    ),
    RecommendedDefault(
        id="codex-plugin-activation",
        introduced_in="0.1.0",
        title="Activate the Yoetz Codex plugin",
        summary="Register and enable the installed Yoetz plugin after an exact preview.",
        kind="activation",
        is_satisfied=_activation_satisfied,
    ),
    RecommendedDefault(
        id="package-update",
        introduced_in="0.1.0",
        title="Update Yoetz",
        summary="Upgrade to the newer PyPI release reported by the policy-gated update check.",
        kind="package_update",
        is_satisfied=_package_update_satisfied,
    ),
)
_BY_ID: Final = MappingProxyType({item.id: item for item in RECOMMENDED_DEFAULTS})


def recommendation_by_id(recommendation_id: str) -> RecommendedDefault | None:
    if type(recommendation_id) is not str:
        return None
    return _BY_ID.get(recommendation_id)


def _store_path(root: Path | None) -> Path:
    return (state_dir() if root is None else root) / _STORE_NAME


def _empty_state() -> RecommendationState:
    return RecommendationState()


def _parse_timestamp(raw: object) -> datetime:
    if type(raw) is not str or not raw or len(raw) > 64:
        raise ValueError("timestamp_invalid")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_naive")
    return parsed.astimezone(UTC)


def _parse_state(parsed: object) -> RecommendationState:
    if type(parsed) is not dict:
        raise ValueError("document_invalid")
    document = cast(dict[str, object], parsed)
    if set(document) != {"schema", "last_evaluated_version", "decisions", "pending"}:
        raise ValueError("document_keys_invalid")
    if document["schema"] != _SCHEMA:
        raise ValueError("schema_invalid")
    version = document["last_evaluated_version"]
    if version is not None and (
        type(version) is not str or not version or len(version) > _MAX_TEXT_LENGTH
    ):
        raise ValueError("version_invalid")
    raw_decisions = document["decisions"]
    if type(raw_decisions) is not dict:
        raise ValueError("decisions_invalid")
    decision_rows = cast(dict[object, object], raw_decisions)
    if len(decision_rows) > _MAX_DECISIONS:
        raise ValueError("decisions_invalid")
    decisions: dict[str, RecommendationDecision] = {}
    for key, raw in decision_rows.items():
        if type(key) is not str or _ID.fullmatch(key) is None or type(raw) is not dict:
            raise ValueError("decision_invalid")
        row = cast(dict[str, object], raw)
        if set(row) != {"decision", "decided_at", "version"}:
            raise ValueError("decision_keys_invalid")
        decision = row["decision"]
        decided_version = row["version"]
        if decision not in {"accepted", "declined"}:
            raise ValueError("decision_value_invalid")
        if (
            type(decided_version) is not str
            or not decided_version
            or len(decided_version) > _MAX_TEXT_LENGTH
        ):
            raise ValueError("decision_version_invalid")
        decisions[key] = RecommendationDecision(
            decision=cast(RecommendationDecisionValue, decision),
            decided_at=_parse_timestamp(row["decided_at"]),
            version=decided_version,
        )
    raw_pending = document["pending"]
    if type(raw_pending) is not list:
        raise ValueError("pending_invalid")
    pending_rows = cast(list[object], raw_pending)
    if len(pending_rows) > len(RECOMMENDED_DEFAULTS):
        raise ValueError("pending_invalid")
    pending: list[str] = []
    for item in pending_rows:
        if type(item) is not str or item not in _BY_ID or item in pending:
            raise ValueError("pending_item_invalid")
        pending.append(item)
    return RecommendationState(
        last_evaluated_version=version,
        decisions=MappingProxyType(decisions),
        pending=tuple(pending),
    )


def load_recommendation_state(*, root: Path | None = None) -> RecommendationState:
    """Load the strict bounded state; missing is empty and corruption is never overwritten."""

    path = _store_path(root)
    if path.parent.exists():
        try:
            ensure_owner_only_dir(path.parent)
        except PathSafetyError as exc:
            raise RecommendationStoreError("recommendation_store_unsafe") from exc
    try:
        facts = path.lstat()
        if (
            stat.S_ISLNK(facts.st_mode)
            or not stat.S_ISREG(facts.st_mode)
            or facts.st_uid != os.geteuid()
            or stat.S_IMODE(facts.st_mode) & 0o077
        ):
            raise RecommendationStoreError("recommendation_store_unsafe")
        raw = path.read_bytes()
    except FileNotFoundError:
        return _empty_state()
    except RecommendationStoreError:
        raise
    except OSError as exc:
        raise RecommendationStoreError("recommendation_store_unsafe") from exc
    if not raw or len(raw) > _MAX_STORE_BYTES:
        raise RecommendationStoreError("recommendation_store_corrupt")
    try:
        return _parse_state(json.loads(raw.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RecommendationStoreError("recommendation_store_corrupt") from exc


@contextmanager
def _state_lock(root: Path | None) -> Generator[None]:
    """Serialize recommendation read-modify-write transitions across local processes."""

    lock_path = _store_path(root).with_name(_LOCK_NAME)
    descriptor: int | None = None
    try:
        ensure_owner_only_dir(lock_path.parent)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        facts = os.fstat(descriptor)
        if (
            not stat.S_ISREG(facts.st_mode)
            or facts.st_uid != os.geteuid()
            or stat.S_IMODE(facts.st_mode) & 0o077
        ):
            raise RecommendationStoreError("recommendation_store_unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except RecommendationStoreError:
        raise
    except PathSafetyError as exc:
        raise RecommendationStoreError("recommendation_store_unsafe") from exc
    except OSError as exc:
        raise RecommendationStoreError("recommendation_store_unsafe") from exc
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("recommendation_decision_time_naive")
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _state_payload(state: RecommendationState) -> bytes:
    document = {
        "schema": _SCHEMA,
        "last_evaluated_version": state.last_evaluated_version,
        "decisions": {
            key: {
                "decision": row.decision,
                "decided_at": _timestamp_text(row.decided_at),
                "version": row.version,
            }
            for key, row in sorted(state.decisions.items())
        },
        "pending": list(state.pending),
    }
    payload = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    if len(payload) > _MAX_STORE_BYTES:
        raise RecommendationStoreError("recommendation_store_write_failed")
    return payload


def store_recommendation_state(state: RecommendationState, *, root: Path | None = None) -> Path:
    """Atomically persist owner-only recommendation state."""

    if type(state) is not RecommendationState:
        raise TypeError("recommendation_state_wrong_type")
    path = _store_path(root)
    payload = _state_payload(state)
    try:
        ensure_owner_only_dir(path.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".yoetz-recommendations-", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    except PathSafetyError as exc:
        raise RecommendationStoreError("recommendation_store_unsafe") from exc
    except OSError as exc:
        raise RecommendationStoreError("recommendation_store_write_failed") from exc
    return path


async def evaluate_recommendation_context(
    *,
    observation_enabled: bool | None = None,
    codex_activation_state: str | None = None,
    policy: PrivacyPolicy | None = None,
    network_egress_permitted: bool | None = None,
    update_checks_enabled: bool | None = None,
    allow_network: bool = True,
    cache_root: Path | None = None,
) -> RecommendationContext:
    """Resolve the existing package advisory and combine dependency-injected local facts."""

    advisory_kwargs: dict[str, object] = {}
    if cache_root is not None:
        advisory_kwargs["cache_root"] = cache_root
    package_update = await resolve_package_update_advisory(
        policy=policy,
        network_egress_permitted=network_egress_permitted,
        update_checks_enabled=update_checks_enabled,
        allow_network=allow_network,
        **advisory_kwargs,  # type: ignore[arg-type]
    )
    return RecommendationContext(
        observation_enabled=observation_enabled,
        codex_activation_state=codex_activation_state,
        package_update=package_update,
    )


def _decision_suppresses(
    decision: RecommendationDecision | None, *, installed_version: str
) -> bool:
    if decision is None:
        return False
    if decision.decision == "declined":
        return True
    # Acceptance authorizes one exact action. Suppress it for this running version so a package
    # upgrade command does not immediately re-nag; a new release frontier is evaluated afresh.
    return decision.version == installed_version


def _fact_is_known(item: RecommendedDefault, context: RecommendationContext) -> bool:
    if item.id == "observation-enabled":
        return context.observation_enabled is not None
    if item.id == "codex-plugin-activation":
        return context.codex_activation_state is not None
    if item.id == "package-update":
        return context.package_update is not None
    return False


async def refresh_pending(
    *,
    context: RecommendationContext | None = None,
    context_factory: RecommendationContextFactory | None = None,
    root: Path | None = None,
    version: str | None = None,
    force: bool = False,
) -> RecommendationState:
    """Recompute at a release frontier or while pending advice still needs reconciliation."""

    if context is not None and context_factory is not None:
        raise ValueError("recommendation_context_ambiguous")
    current_version = installed_package_version() if version is None else version
    if type(current_version) is not str or not current_version:
        raise ValueError("recommendation_version_invalid")
    snapshot = load_recommendation_state(root=root)
    if snapshot.last_evaluated_version == current_version and not snapshot.pending and not force:
        return snapshot
    resolved = (
        await context_factory()
        if context_factory is not None
        else (context if context is not None else RecommendationContext())
    )
    with _state_lock(root):
        current = load_recommendation_state(root=root)
        pending_ids: list[str] = []
        for item in RECOMMENDED_DEFAULTS:
            if _decision_suppresses(
                current.decisions.get(item.id), installed_version=current_version
            ):
                continue
            if not _fact_is_known(item, resolved):
                # An authority-limited heavy touchpoint (for example the daemon,
                # which does not own an exact selected Codex home) must not erase
                # a recommendation established by an earlier exact evaluation.
                if item.id in current.pending:
                    pending_ids.append(item.id)
                continue
            if not item.is_satisfied(resolved):
                pending_ids.append(item.id)
        pending = tuple(pending_ids)
        updated = RecommendationState(
            last_evaluated_version=current_version,
            decisions=current.decisions,
            pending=pending,
        )
        store_recommendation_state(updated, root=root)
        return updated


def record_recommendation_decision(
    recommendation_id: str,
    decision: RecommendationDecisionValue,
    *,
    root: Path | None = None,
    version: str | None = None,
    now: datetime | None = None,
) -> RecommendationState:
    """Record an explicit accept/decline and remove it from cached pending advice."""

    if recommendation_id not in _BY_ID:
        raise ValueError("recommendation_unknown")
    if decision not in {"accepted", "declined"}:
        raise ValueError("recommendation_decision_invalid")
    current_version = installed_package_version() if version is None else version
    if type(current_version) is not str or not current_version:
        raise ValueError("recommendation_version_invalid")
    with _state_lock(root):
        current = load_recommendation_state(root=root)
        decisions = dict(current.decisions)
        decisions[recommendation_id] = RecommendationDecision(
            decision=decision,
            decided_at=now if now is not None else datetime.now(tz=UTC),
            version=current_version,
        )
        updated = RecommendationState(
            last_evaluated_version=current.last_evaluated_version or current_version,
            decisions=MappingProxyType(decisions),
            pending=tuple(item for item in current.pending if item != recommendation_id),
        )
        store_recommendation_state(updated, root=root)
        return updated


def cached_pending_recommendations(
    *, root: Path | None = None, limit: int | None = None
) -> tuple[RecommendedDefault, ...]:
    """Read cached advice only; hook callers may exception-suppress this small-file operation."""

    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("recommendation_limit_invalid")
    state = load_recommendation_state(root=root)
    pending = tuple(_BY_ID[item] for item in state.pending)
    return pending if limit is None else pending[:limit]

"""Isolated production composition for the named multi-agent conformance scenarios.

The vault, catalog, encrypted objects, task ledgers and runtime routing are real. Only the
clock and singleton generation store are deterministic; no user service or user data is used.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yoetz.adapters.keys.encrypted_vault import EncryptedVaultStore
from yoetz.adapters.keys.secret_memory import LocalSecretMemory
from yoetz.application.service import Application
from yoetz.config.models import YoetzConfig
from yoetz.ports.control import ServiceState
from yoetz.ports.diagnostics import StartupCheckResult
from yoetz.ports.secret_memory import SecretPurpose
from yoetz.service.lifecycle import ServiceLifecycle
from yoetz.service.ready_composition import build_ready_application_factory
from yoetz.service.vault import VaultMode, VaultService

INSTALLATION_ID = "ins_58000000-0000-4000-8000-000000000001"
INSTANCE_ID = "svc_58000000-0000-4000-8000-000000000001"


@dataclass
class ScenarioClock:
    instant: datetime = datetime(2026, 9, 5, 12, tzinfo=UTC)
    elapsed: float = 1.0

    def now_utc(self) -> datetime:
        return self.instant

    def monotonic_seconds(self) -> float:
        return self.elapsed

    def advance(self, *, seconds: int) -> None:
        self.instant += timedelta(seconds=seconds)
        self.elapsed += seconds


class _Generations:
    def __init__(self) -> None:
        self.current = 0

    def advance(self, instance_id: str) -> int:
        assert instance_id == INSTANCE_ID
        self.current += 1
        return self.current


@dataclass(frozen=True)
class _Paths:
    bundle: Path

    @property
    def state(self) -> Path:
        return self.bundle / "state"


class _Diagnostics:
    def record(self, result: StartupCheckResult) -> None:
        assert isinstance(result, StartupCheckResult)


@dataclass
class MultiAgentService:
    app: Application
    clock: ScenarioClock
    vault: VaultService
    lifecycle: ServiceLifecycle
    root: Path


@contextmanager
def private_service_root() -> Generator[Path]:
    """Yield a short owner-only service root outside shared pytest temp space.

    Linux CI places ``tmp_path`` below shared ``/tmp``, which production validation rejects.
    Keep the synthetic vault/catalog/state tree in a namespaced home-cache directory instead.
    """

    base = (Path.home() / ".cache" / "yoetz-conformance").resolve()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    base.chmod(0o700)
    root = Path(tempfile.mkdtemp(prefix="yz-", dir=base))
    root.chmod(0o700)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=False)


@asynccontextmanager
async def multi_agent_service(
    root: Path, *, config: YoetzConfig | None = None
) -> AsyncGenerator[MultiAgentService]:
    del root  # callers retain pytest labels; private service data cannot live below shared /tmp.
    with private_service_root() as root:
        clock = ScenarioClock()
        memory = LocalSecretMemory()
        lifecycle = ServiceLifecycle(
            clock,
            generation_store=_Generations(),
            process_start_identity_commitment="sha256:" + "a" * 64,
            instance_id=INSTANCE_ID,
        )
        vault: VaultService | None = None
        app: Application | None = None
        try:
            await lifecycle.acquire_singleton()
            await lifecycle.transition(ServiceState.LOCKED)
            vault = VaultService(
                installation_id=INSTALLATION_ID,
                service_generation=1,
                mode=VaultMode.UNINITIALIZED,
                secret_memory=memory,
                clock=clock,
                vault_store_factory=lambda: EncryptedVaultStore(root / "vault"),
                pristine_state_digest="sha256:" + "b" * 64,
            )
            initialize = memory.capture(
                SecretPurpose.VAULT_INITIALIZE, bytearray(b"synthetic conformance vault only")
            )
            await vault.initialize_passphrase(initialize, "sha256:" + "c" * 64)
            assert vault is not None
            factory = build_ready_application_factory(
                lifecycle=lifecycle,
                vault=vault,
                config=config or YoetzConfig(),
                paths=_Paths(root),
                clock=clock,
                secret_memory=memory,
                diagnostics=_Diagnostics(),
            )
            app = await factory(1, vault.generation)
            yield MultiAgentService(app, clock, vault, lifecycle, root)
        finally:
            if app is not None:
                await app.close()
            if vault is not None:
                await vault.close()
            memory.close()
            await lifecycle.close()

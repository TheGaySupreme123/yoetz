"""Issue #855: evaluator runtime selection, retention, repair, and precise diagnosis.

The reproduced incident is replayed end to end through the real configuration loader and writer:
a v1 binding whose ordinary npm path now holds a newer Codex, with a valid login and custom
timeout/retry budgets. Codex processes, discovery, and package-manager downloads are stubbed; the
admitted digest is substituted for synthetic bytes, and every store lives under ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from yoetz.adapters.providers import codex_app_server
from yoetz.adapters.providers import codex_evaluator_runtime as runtime_store
from yoetz.adapters.providers.codex_app_server import (
    CODEX_EVALUATOR_CONFIG,
    CodexAppServerProfile,
    CodexEvaluatorCell,
    CodexRuntimeStatus,
    codex_evaluator_cell_for_platform,
)
from yoetz.cli import codex_subscription as module
from yoetz.config.models import (
    ExternalRuntimeProfileConfig,
    SemanticFallbackConfig,
    StorageConfig,
    YoetzConfig,
)
from yoetz.config.write import codex_subscription_runtime, render_config_toml
from yoetz.ports.harness_mcp import HarnessBinary
from yoetz.ports.integrations import HarnessId

pytestmark = pytest.mark.anyio

_ADMITTED = b"admitted codex 0.150.1 native bytes"
_NEWER = b"codex-cli 0.153.4 that replaced the ordinary npm path"
_OWNER_CHOICES = ("model", "reasoning_effort", "timeout_seconds", "max_retries", "codex_home")
_V1 = {
    "capability_profile": "codex-evaluator/0.150.1/v1",
    "capability_cell_sha256": "sha256:" + "1" * 64,
}


def _plain(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class _Codex:
    """Recording stand-ins for every Codex app-server process entry point."""

    signed_in: bool = True
    cleanup: str = "terminated"
    probes: list[CodexAppServerProfile] = field(default_factory=lambda: [])
    logins: list[CodexAppServerProfile] = field(default_factory=lambda: [])
    logouts: list[CodexAppServerProfile] = field(default_factory=lambda: [])

    async def account_status(self, profile: CodexAppServerProfile) -> CodexRuntimeStatus:
        self.probes.append(profile)
        return CodexRuntimeStatus(
            True,
            "chatgpt" if self.signed_in else None,
            "pro" if self.signed_in else None,
            self.signed_in,
            self.cleanup,  # pyright: ignore[reportArgumentType]
        )

    async def login(self, profile: CodexAppServerProfile, **_kwargs: object) -> CodexRuntimeStatus:
        self.logins.append(profile)
        return CodexRuntimeStatus(True, "chatgpt", "pro", True, "terminated")

    async def logout(self, profile: CodexAppServerProfile) -> CodexRuntimeStatus:
        self.logouts.append(profile)
        return CodexRuntimeStatus(True, None, None, False, "terminated")


@dataclass
class _Env:
    tmp: Path
    cell: CodexEvaluatorCell
    bundle: Path
    config_path: Path
    codex: _Codex
    discovered: list[Path]
    downloads: list[Path | None]

    @property
    def managed(self) -> Path:
        return runtime_store.managed_runtime_path(self.bundle, self.cell)

    def executable(self, relative: str, data: bytes = _ADMITTED) -> Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o700)
        return path

    def home(self) -> Path:
        home = self.tmp / "evaluator-home"
        home.mkdir(mode=0o700, exist_ok=True)
        (home / "config.toml").write_bytes(CODEX_EVALUATOR_CONFIG.encode())
        return home

    def binding(self, executable: Path, **overrides: object) -> ExternalRuntimeProfileConfig:
        binding = codex_subscription_runtime(
            executable_path=str(executable),
            executable_sha256=self.cell.executable_sha256,
            runtime_version="0.150.1",
            source_identity=self.cell.source_identity,
            app_server_schema_sha256=self.cell.app_server_schema_sha256,
            capability_cell_sha256=self.cell.capability_cell_sha256,
            isolated_config_sha256=self.cell.isolated_config_sha256,
            capability_profile=self.cell.capability_profile,
            capability_evidence_expires_at="2026-11-30T00:00:00Z",
            codex_home=str(self.home()),
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            timeout_seconds=37,
            max_retries=0,
        )
        return binding.model_copy(update=overrides)

    def write_config(
        self,
        binding: ExternalRuntimeProfileConfig | None,
        *,
        fallback_behind_api: bool = False,
    ) -> YoetzConfig:
        from unit.cli.test_provider_status import _provider  # pyright: ignore[reportPrivateUsage]

        config = YoetzConfig(
            profile="strict-local" if binding is None else "codex-subscription",
            storage=StorageConfig(data_dir=self.tmp / "state"),
            external_runtime=binding,
        )
        if fallback_behind_api:
            config = config.model_copy(
                update={
                    "profile": "local-openai",
                    "provider": _provider(),
                    "semantic_fallback": SemanticFallbackConfig(primary="api_provider"),
                }
            )
        self.config_path.write_text(render_config_toml(config), encoding="utf-8")
        return config

    def written(self) -> YoetzConfig:
        return module._base_config(self.config_path)  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Env:
    for name in tuple(os.environ):
        if name.startswith("YOETZ_"):
            monkeypatch.delenv(name)
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("YOETZ_CONFIG", str(config_path))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    cell = codex_evaluator_cell_for_platform("linux", "x86_64")

    def admitted_digest(path: Path) -> str:
        data = path.read_bytes()
        return cell.executable_sha256 if data == _ADMITTED else _plain(data)

    real_descriptor_digest = runtime_store._digest_descriptor  # pyright: ignore[reportPrivateUsage]

    def descriptor_digest(descriptor: int, sink: object = None) -> str:
        value = real_descriptor_digest(descriptor, sink)  # pyright: ignore[reportArgumentType]
        return cell.executable_sha256 if value == _plain(_ADMITTED) else value

    def allow_private(_path: Path) -> None:
        return None

    monkeypatch.setattr(module, "_sha256_file", admitted_digest)
    monkeypatch.setattr(codex_app_server, "_sha256_file", admitted_digest)
    monkeypatch.setattr(runtime_store, "_digest_descriptor", descriptor_digest)
    monkeypatch.setattr(runtime_store, "verify_private_local_bundle", allow_private)
    monkeypatch.setattr(codex_app_server, "verify_private_local_bundle", allow_private)
    bundle = tmp_path / "bundle"

    def runtime_bundle(_config: YoetzConfig | None = None) -> Path:
        return bundle

    monkeypatch.setattr(module, "runtime_bundle", runtime_bundle)

    codex = _Codex()
    monkeypatch.setattr(module, "codex_account_status", codex.account_status)
    monkeypatch.setattr(module, "codex_login", codex.login)
    monkeypatch.setattr(module, "codex_logout", codex.logout)

    discovered: list[Path] = []
    monkeypatch.setattr(
        "yoetz.adapters.integrations.codex_discovery.discover_codex_binaries",
        lambda: tuple(
            HarnessBinary(HarnessId.CODEX, str(path), None, "untested") for path in discovered
        ),
    )
    downloads: list[Path | None] = []

    def provision(**kwargs: object) -> Path:
        downloads.append(cast(Path | None, kwargs.get("npm")))
        source = tmp_path / "npm-cache" / "codex"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(_ADMITTED)
        source.chmod(0o700)
        return runtime_store.retain_codex_runtime(
            source,
            bundle=cast(Path, kwargs["bundle"]),
            cell=cast(CodexEvaluatorCell, kwargs["cell"]),
        )

    monkeypatch.setattr(module, "provision_codex_runtime", provision)
    return _Env(tmp_path, cell, bundle, config_path, codex, discovered, downloads)


# -- the reproduced incident --------------------------------------------------------------------


async def test_repair_rebinds_a_stranded_v1_binding_keeping_login_and_every_choice(
    env: _Env,
) -> None:
    ordinary_npm = env.executable("npm-prefix/codex", _NEWER)
    stranded = env.binding(ordinary_npm, **_V1)
    env.write_config(stranded)
    evaluator = env.executable("separate-prefix/codex")

    before = module.diagnose_bound_runtime(stranded)
    assert (before.state, before.capability, before.executable) == (
        "codex_runtime_executable_changed",
        "profile_outdated",
        "changed",
    )

    result = await module.codex_subscription_repair(
        config_path=env.config_path, executable=evaluator
    )

    repaired = env.written().external_runtime
    assert repaired is not None
    assert repaired.executable_path == str(env.managed)
    assert repaired.capability_profile == env.cell.capability_profile
    assert repaired.capability_cell_sha256 == env.cell.capability_cell_sha256
    # Exactly the three fields the issue's manual repair changed; nothing else moved.
    assert result["changed_fields"] == [
        "executable_path",
        "capability_cell_sha256",
        "capability_profile",
    ]
    assert (repaired.model, repaired.reasoning_effort) == ("gpt-5.6-sol", "xhigh")
    assert (repaired.timeout_seconds, repaired.max_retries) == (37, 0)
    assert repaired.codex_home == stranded.codex_home
    assert result["login_reused"] is True
    assert result["state_before"] == "codex_runtime_executable_changed"
    assert env.codex.logins == [] and env.codex.logouts == []
    (probe,) = env.codex.probes
    assert probe.executable_path == env.managed
    assert probe.codex_home == Path(stranded.codex_home)
    assert module.diagnose_bound_runtime(repaired).ready
    # The host installation is untouched; the evaluator runs Yoetz's own verified copy.
    assert ordinary_npm.read_bytes() == _NEWER
    assert env.managed.read_bytes() == _ADMITTED


async def test_a_later_host_update_cannot_strand_a_retained_binding(env: _Env) -> None:
    host = env.executable("npm-prefix/codex")
    env.write_config(None)

    await module.codex_subscription_setup(
        executable=host,
        codex_home=env.home(),
        model="gpt-5.6-luna",
        reasoning_effort="high",
        login_mode="browser",
        open_browser=False,
        switch_account=False,
        config_path=env.config_path,
    )
    host.write_bytes(_NEWER)  # an ordinary `npm install -g @openai/codex@latest`

    bound = env.written().external_runtime
    assert bound is not None and bound.executable_path == str(env.managed)
    assert module.diagnose_bound_runtime(bound).ready
    assert env.codex.logins == []


async def test_repair_refuses_without_a_reusable_login_and_writes_nothing(env: _Env) -> None:
    stranded = env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1)
    env.write_config(stranded)
    preimage = env.config_path.read_bytes()
    env.codex.signed_in = False

    with pytest.raises(ValueError, match="codex_subscription_login_required"):
        await module.codex_subscription_repair(
            config_path=env.config_path, executable=env.executable("evaluator/codex")
        )

    assert env.config_path.read_bytes() == preimage
    assert env.codex.logins == [] and env.codex.logouts == []
    assert module.subscription_remediation("codex_subscription_login_required") is not None


async def test_repair_fails_closed_when_probe_cleanup_is_unconfirmed(env: _Env) -> None:
    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1))
    preimage = env.config_path.read_bytes()
    env.codex.cleanup = "failed"

    with pytest.raises(ValueError, match="codex_subscription_readiness_unproven"):
        await module.codex_subscription_repair(
            config_path=env.config_path, executable=env.executable("evaluator/codex")
        )
    assert env.config_path.read_bytes() == preimage


async def test_repair_preserves_a_concurrent_config_edit(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1))

    async def edit_during_probe(profile: CodexAppServerProfile) -> CodexRuntimeStatus:
        with env.config_path.open("a", encoding="utf-8") as handle:
            handle.write("\n# owner edit\n")
        return await env.codex.account_status(profile)

    monkeypatch.setattr(module, "codex_account_status", edit_during_probe)

    with pytest.raises(ValueError, match="config_preimage_mismatch"):
        await module.codex_subscription_repair(
            config_path=env.config_path, executable=env.executable("evaluator/codex")
        )
    assert "# owner edit" in env.config_path.read_text(encoding="utf-8")


async def test_repair_keeps_the_fallback_role_of_a_pairing(env: _Env) -> None:
    env.write_config(
        env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1), fallback_behind_api=True
    )

    result = await module.codex_subscription_repair(
        config_path=env.config_path, executable=env.executable("evaluator/codex")
    )

    written = env.written()
    assert result["endpoint_role"] == "fallback"
    assert written.semantic_fallback == SemanticFallbackConfig(primary="api_provider")
    assert written.provider is not None


@pytest.mark.parametrize(
    ("fault", "token"),
    [
        ("config_changed", "codex_runtime_config_changed"),
        ("home_missing", "codex_home_missing"),
    ],
)
async def test_hand_fix_states_are_refused_before_any_side_effect(
    env: _Env, fault: str, token: str
) -> None:
    binding = env.binding(env.executable("npm-prefix/codex", _NEWER))
    home = Path(binding.codex_home)
    if fault == "config_changed":
        (home / "config.toml").write_text('approval_policy = "on-request"\n', encoding="utf-8")
    else:
        (home / "config.toml").unlink()
        home.rmdir()
    env.write_config(binding)
    preimage = env.config_path.read_bytes()

    with pytest.raises(ValueError, match=token):
        await module.codex_subscription_repair(
            config_path=env.config_path, executable=env.executable("evaluator/codex")
        )
    assert env.config_path.read_bytes() == preimage
    assert env.codex.probes == []
    assert not env.managed.exists()
    assert module.binding_continuation(token) is not None


async def test_repair_restores_a_missing_isolated_config_without_touching_login(
    env: _Env,
) -> None:
    binding = env.binding(env.executable("npm-prefix/codex", _NEWER))
    (Path(binding.codex_home) / "config.toml").unlink()
    env.write_config(binding)

    await module.codex_subscription_repair(
        config_path=env.config_path, executable=env.executable("evaluator/codex")
    )

    config = Path(binding.codex_home) / "config.toml"
    assert config.read_bytes() == CODEX_EVALUATOR_CONFIG.encode()
    assert env.codex.logins == []


async def test_repair_needs_an_admitted_runtime_and_a_binding(env: _Env) -> None:
    env.write_config(None)
    with pytest.raises(ValueError, match="codex_subscription_not_configured"):
        await module.codex_subscription_repair(config_path=env.config_path)
    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER)))
    env.discovered.append(env.executable("other/codex", _NEWER))
    with pytest.raises(ValueError, match="codex_evaluator_runtime_unavailable"):
        await module.codex_subscription_repair(config_path=env.config_path)


def test_repair_plan_has_no_side_effects(env: _Env) -> None:
    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1))
    preimage = env.config_path.read_bytes()

    plan = module.codex_subscription_repair_plan(
        config_path=env.config_path, executable=env.executable("evaluator/codex")
    )

    assert plan["state_before"] == "codex_runtime_executable_changed"
    assert plan["capability_profile_before"] == "codex-evaluator/0.150.1/v1"
    assert plan["capability_profile_after"] == env.cell.capability_profile
    assert plan["executable_path_after"] == str(env.managed)
    preserved = cast(Mapping[str, object], plan["preserved"])
    assert {name: preserved[name] for name in _OWNER_CHOICES} == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "xhigh",
        "timeout_seconds": 37,
        "max_retries": 0,
        "codex_home": str(env.tmp / "evaluator-home"),
    }
    assert "executable_path" not in preserved and "capability_profile" not in preserved
    assert env.config_path.read_bytes() == preimage
    assert not env.bundle.exists()
    assert env.codex.probes == []


# -- selection and preservation -----------------------------------------------------------------


def test_rebinding_preserves_timeout_and_retry_budgets(env: _Env) -> None:
    """The 37-second / zero-retry reproduction from the issue."""

    existing = env.binding(env.executable("npm-prefix/codex"))
    rebuilt = module._binding(  # pyright: ignore[reportPrivateUsage]
        executable=Path(existing.executable_path),
        codex_home=Path(existing.codex_home),
        model=existing.model,
        reasoning_effort=existing.reasoning_effort,
        existing=existing,
    )
    fresh = module._binding(  # pyright: ignore[reportPrivateUsage]
        executable=Path(existing.executable_path),
        codex_home=Path(existing.codex_home),
        model=existing.model,
        reasoning_effort=existing.reasoning_effort,
    )

    defaults = inspect.signature(codex_subscription_runtime).parameters
    assert (rebuilt.timeout_seconds, rebuilt.max_retries) == (37, 0)
    assert (fresh.timeout_seconds, fresh.max_retries) == (
        defaults["timeout_seconds"].default,
        defaults["max_retries"].default,
    )


async def test_setup_rerun_preserves_budgets_and_effort_defaults(env: _Env) -> None:
    env.write_config(env.binding(env.executable("npm-prefix/codex")))
    assert module.default_codex_subscription_reasoning_effort(env.config_path) == "xhigh"
    assert module.default_codex_subscription_model(env.config_path) == "gpt-5.6-sol"

    await module.codex_subscription_setup(
        executable=None,
        codex_home=env.home(),
        model="gpt-5.6-sol",
        reasoning_effort=module.default_codex_subscription_reasoning_effort(env.config_path),
        login_mode="browser",
        open_browser=False,
        switch_account=False,
        config_path=env.config_path,
    )

    bound = env.written().external_runtime
    assert bound is not None
    assert (bound.timeout_seconds, bound.max_retries, bound.reasoning_effort) == (37, 0, "xhigh")
    assert bound.executable_path == str(env.managed)


def test_selection_prefers_eligibility_and_ownership_not_path_order(env: _Env) -> None:
    config = env.write_config(None)
    assert module.select_codex_evaluator_executable(config) is None

    # Lexically first, but not the admitted cell: never offered.
    env.discovered.append(env.executable("a-first/codex", _NEWER))
    assert module.select_codex_evaluator_executable(config) is None
    admitted = env.executable("z-last/codex")
    env.discovered.append(admitted)
    assert module.select_codex_evaluator_executable(config) == ("discovered", admitted)

    bound = env.executable("bound/codex")
    config = env.write_config(env.binding(bound))
    assert module.select_codex_evaluator_executable(config) == ("binding", bound)

    runtime_store.retain_codex_runtime(admitted, bundle=env.bundle, cell=env.cell)
    assert module.select_codex_evaluator_executable(config) == ("managed", env.managed)


async def test_setup_without_an_eligible_runtime_fails_before_any_side_effect(env: _Env) -> None:
    env.write_config(None)
    env.discovered.append(env.executable("host/codex", _NEWER))
    preimage = env.config_path.read_bytes()

    with pytest.raises(ValueError, match="codex_evaluator_runtime_unavailable"):
        await module.codex_subscription_setup(
            executable=None,
            codex_home=env.tmp / "new-home",
            model="gpt-5.6-luna",
            reasoning_effort="high",
            login_mode="browser",
            open_browser=False,
            switch_account=False,
            config_path=env.config_path,
        )

    assert env.config_path.read_bytes() == preimage
    assert not (env.tmp / "new-home").exists()
    assert env.codex.probes == [] and env.codex.logins == []


# -- status, disconnect, runtime commands -------------------------------------------------------


async def test_status_names_the_structural_cause_before_starting_codex(env: _Env) -> None:
    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1))

    with pytest.raises(ValueError, match="codex_runtime_executable_changed"):
        await module.codex_subscription_status(config_path=env.config_path)
    assert env.codex.probes == []


async def test_disconnect_logs_out_a_stranded_home_through_the_admitted_runtime(
    env: _Env,
) -> None:
    stranded = env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1)
    env.write_config(stranded)
    runtime_store.retain_codex_runtime(
        env.executable("evaluator/codex"), bundle=env.bundle, cell=env.cell
    )

    result = await module.codex_subscription_disconnect(config_path=env.config_path)

    (logout,) = env.codex.logouts
    assert logout.executable_path == env.managed
    assert logout.codex_home == Path(stranded.codex_home)
    assert result["binding_removed"] is True
    assert env.written().external_runtime is None


async def test_disconnect_without_an_admitted_runtime_keeps_the_binding(env: _Env) -> None:
    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1))
    preimage = env.config_path.read_bytes()

    with pytest.raises(ValueError, match="codex_runtime_executable_changed"):
        await module.codex_subscription_disconnect(config_path=env.config_path)
    assert env.codex.logouts == []
    assert env.config_path.read_bytes() == preimage


def test_runtime_status_names_the_next_command_without_starting_codex(env: _Env) -> None:
    env.write_config(None)
    empty = module.codex_evaluator_runtime_status(config_path=env.config_path)
    assert empty["binding"] is None
    assert empty["next_command"] == "yoetz provider codex-subscription runtime install"
    assert empty["login_checked"] is False

    host = env.executable("npm-prefix/codex")
    env.write_config(env.binding(host))
    unmanaged = module.codex_evaluator_runtime_status(config_path=env.config_path)
    binding = cast(Mapping[str, object], unmanaged["binding"])
    assert binding["state"] == "ready" and binding["uses_managed_runtime"] is False
    # Ready today, but a host update would strand it: repair moves it onto the retained copy.
    assert unmanaged["next_command"] == "yoetz provider codex-subscription repair"

    host.write_bytes(_NEWER)
    stranded = module.codex_evaluator_runtime_status(config_path=env.config_path)
    assert cast(Mapping[str, object], stranded["binding"])["state"] == (
        "codex_runtime_executable_changed"
    )
    assert stranded["next_command"] == "yoetz provider codex-subscription repair"

    runtime_store.retain_codex_runtime(
        env.executable("evaluator/codex"), bundle=env.bundle, cell=env.cell
    )
    env.write_config(env.binding(env.managed))
    managed = module.codex_evaluator_runtime_status(config_path=env.config_path)
    assert cast(Mapping[str, object], managed["managed_runtime"])["state"] == "verified"
    assert cast(Mapping[str, object], managed["binding"])["uses_managed_runtime"] is True
    assert managed["next_command"] is None
    assert env.codex.probes == []


def test_runtime_install_retains_local_bytes_or_downloads_only_when_authorized(
    env: _Env,
) -> None:
    env.write_config(None)
    with pytest.raises(ValueError, match="codex_evaluator_runtime_unavailable"):
        module.codex_evaluator_runtime_install_plan(
            source=None, download=False, config_path=env.config_path
        )
    with pytest.raises(ValueError, match="codex_runtime_capability_unsupported"):
        module.codex_evaluator_runtime_install(
            source=env.executable("newer/codex", _NEWER),
            npm=None,
            download=False,
            config_path=env.config_path,
        )
    assert not env.managed.exists()

    plan = module.codex_evaluator_runtime_install_plan(
        source=None, download=True, config_path=env.config_path
    )
    assert plan["download"] is True and plan["source_path"] is None
    installed = module.codex_evaluator_runtime_install(
        source=None, npm=Path("/opt/npm/bin/npm"), download=True, config_path=env.config_path
    )
    assert env.downloads == [Path("/opt/npm/bin/npm")]
    assert installed["managed_runtime_path"] == str(env.managed)
    assert installed["managed_runtime_state"] == "verified"
    assert installed["binding_changed"] is False
    assert installed["next_command"] == "yoetz provider codex-subscription setup"

    # A verified copy now satisfies later installs locally; nothing is downloaded again.
    again = module.codex_evaluator_runtime_install(
        source=None, npm=None, download=True, config_path=env.config_path
    )
    assert again["source"] == "managed"
    assert env.downloads == [Path("/opt/npm/bin/npm")]


def test_runtime_remove_refuses_while_bound_and_removes_only_the_copy(env: _Env) -> None:
    runtime_store.retain_codex_runtime(
        env.executable("evaluator/codex"), bundle=env.bundle, cell=env.cell
    )
    env.write_config(env.binding(env.managed))
    with pytest.raises(ValueError, match="codex_evaluator_runtime_in_use"):
        module.codex_evaluator_runtime_remove(config_path=env.config_path)
    assert env.managed.exists()

    module.codex_subscription_rollback(config_path=env.config_path)
    home = env.tmp / "evaluator-home"
    removed = module.codex_evaluator_runtime_remove(config_path=env.config_path)
    assert removed["removed"] is True
    assert not env.managed.exists()
    assert (home / "config.toml").exists()


# -- operator surfaces --------------------------------------------------------------------------


def test_failure_lines_name_the_cause_and_next_step() -> None:
    line = module.subscription_failure_line(ValueError("codex_runtime_executable_changed"))
    assert line.startswith("codex_subscription: codex_runtime_executable_changed: ")
    assert "repair" in line
    for state, command in module.BINDING_CONTINUATIONS.items():
        assert module.subscription_remediation(state) is not None, state
        assert command.startswith("yoetz "), state
    assert module.binding_continuation("ready") is None


def test_cli_repair_needs_explicit_acceptance_and_recomposes_after(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yoetz.cli.app import app

    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1))
    runtime_store.retain_codex_runtime(
        env.executable("evaluator/codex"), bundle=env.bundle, cell=env.cell
    )
    restarts: list[str] = []

    async def restart() -> dict[str, object]:
        restarts.append("restart")
        return {"reachable": True, "state": "ready", "vault_mode": None}

    monkeypatch.setattr("yoetz.cli.setup.restart_service_for_semantic_composition", restart)
    runner = CliRunner()
    preimage = env.config_path.read_bytes()

    refused = runner.invoke(app, ["provider", "codex-subscription", "repair"])
    assert refused.exit_code == 20
    assert env.config_path.read_bytes() == preimage
    assert restarts == []

    accepted = runner.invoke(app, ["provider", "codex-subscription", "repair", "--accept"])
    assert accepted.exit_code == 0, accepted.output
    assert "changed fields: executable_path, capability_cell_sha256, capability_profile" in (
        accepted.output
    )
    assert restarts == ["restart"]
    rewritten = tomllib.loads(env.config_path.read_text(encoding="utf-8"))
    assert rewritten["external_runtime"]["executable_path"] == str(env.managed)
    assert rewritten["external_runtime"]["timeout_seconds"] == 37


def test_cli_runtime_status_exit_code_follows_the_binding(env: _Env) -> None:
    from yoetz.cli.app import app

    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER)))
    runner = CliRunner()
    stranded = runner.invoke(app, ["provider", "codex-subscription", "runtime", "status", "--json"])
    assert stranded.exit_code == 20
    assert "codex_runtime_executable_changed" in stranded.stdout

    env.write_config(None)
    unbound = runner.invoke(app, ["provider", "codex-subscription", "runtime", "status", "--json"])
    assert unbound.exit_code == 0


def test_cli_failure_renders_the_subscription_remediation(env: _Env) -> None:
    from yoetz.cli.app import app

    env.write_config(env.binding(env.executable("npm-prefix/codex", _NEWER), **_V1))
    result = CliRunner().invoke(app, ["provider", "codex-subscription", "status"])

    assert result.exit_code == 20
    assert "codex_subscription: codex_runtime_executable_changed: " in result.stderr
    assert "yoetz provider codex-subscription repair" in result.stderr
    assert str(env.tmp) not in result.stderr


def test_guided_setup_offers_only_an_eligible_runtime_or_the_consented_download(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.write_config(None)
    env.discovered.append(env.executable("host/codex", _NEWER))
    prompts: list[tuple[str, object]] = []
    answers = {"Download the evaluator runtime now?": False}

    def prompt(message: str, **kwargs: object) -> str:
        prompts.append((message, kwargs.get("default")))
        raise KeyboardInterrupt

    def confirm(message: str, **_kwargs: object) -> bool:
        return answers[message]

    monkeypatch.setattr("typer.prompt", prompt)
    monkeypatch.setattr("typer.confirm", confirm)

    with pytest.raises(KeyboardInterrupt):
        module._prompt_evaluator_executable()  # pyright: ignore[reportPrivateUsage]
    # The newer host binary is not offered; declining the download leaves no default.
    assert prompts == [("Codex evaluator executable", None)]
    assert env.downloads == []

    answers["Download the evaluator runtime now?"] = True
    prompts.clear()
    with pytest.raises(KeyboardInterrupt):
        module._prompt_evaluator_executable()  # pyright: ignore[reportPrivateUsage]
    assert prompts == [("Codex evaluator executable", str(env.managed))]
    assert len(env.downloads) == 1

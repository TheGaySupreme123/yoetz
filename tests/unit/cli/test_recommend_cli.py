from __future__ import annotations

import tomllib
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import anyio
from typer.testing import CliRunner

from yoetz.application.package_update import (
    build_package_update_advisory,
    installed_package_version,
)
from yoetz.application.recommendations import (
    RecommendationContext,
    RecommendationState,
    load_recommendation_state,
    refresh_pending,
    store_recommendation_state,
)
from yoetz.cli.app import app
from yoetz.config.models import ConfigError, ObservationConfig, YoetzConfig
from yoetz.config.write import write_config_toml
from yoetz.ports.integrations import IntegrationTarget

_RUNNER = CliRunner()


def _patch_state_root(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.application.recommendations.state_dir", lambda: tmp_path
    )


def _patch_pending_context(monkeypatch: object, recommendation_id: str) -> None:
    async def context(**_kwargs: object) -> RecommendationContext:
        if recommendation_id == "observation-enabled":
            return RecommendationContext(observation_enabled=False)
        if recommendation_id == "codex-plugin-activation":
            return RecommendationContext(codex_activation_state="installed_not_activated")
        return RecommendationContext(
            package_update=build_package_update_advisory(
                installed_version="0.1.0", latest_version="0.2.0", source="cache"
            )
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend._current_context", context
    )


def test_accept_observation_writes_config_and_records_decision(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    _patch_pending_context(monkeypatch, "observation-enabled")
    config_path = tmp_path / "config.toml"
    write_config_toml(YoetzConfig(observation=ObservationConfig(enabled=False)), path=config_path)
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend.config_file_path", lambda: config_path
    )

    result = _RUNNER.invoke(app, ["recommend", "accept", "observation-enabled"])

    assert result.exit_code == 0, result.output
    assert "[observation] enabled = true" in result.stdout
    assert "restart the Yoetz service" in result.stdout
    assert tomllib.loads(config_path.read_text(encoding="utf-8"))["observation"] == {
        "enabled": True
    }
    state = load_recommendation_state(root=tmp_path)
    assert state.decisions["observation-enabled"].decision == "accepted"


def test_decline_is_durable_and_removes_cached_pending(tmp_path: Path, monkeypatch: object) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    _patch_pending_context(monkeypatch, "observation-enabled")
    refresh = RecommendationState(
        last_evaluated_version="0.1.0",
        decisions=MappingProxyType({}),
        pending=("observation-enabled",),
    )
    store_recommendation_state(refresh, root=tmp_path)

    result = _RUNNER.invoke(app, ["recommend", "decline", "observation-enabled"])

    assert result.exit_code == 0, result.output
    assert "will not be shown again" in result.stdout
    state = load_recommendation_state(root=tmp_path)
    assert state.pending == ()
    assert state.decisions["observation-enabled"].decision == "declined"


def test_package_accept_prints_pypi_upgrade_command_only(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    _patch_pending_context(monkeypatch, "package-update")

    result = _RUNNER.invoke(app, ["recommend", "accept", "package-update"])

    assert result.exit_code == 0, result.output
    assert "uv tool upgrade yoetz" in result.stdout
    assert "npm" not in result.stdout


def test_list_renders_pending_actions(monkeypatch: object) -> None:
    async def fake_refresh(**_kwargs: object) -> RecommendationState:
        return RecommendationState(
            last_evaluated_version="0.1.0",
            pending=("observation-enabled",),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend._refresh_for_cli", fake_refresh
    )

    result = _RUNNER.invoke(app, ["recommend", "list"])

    assert result.exit_code == 0, result.output
    assert "Enable local observation" in result.stdout
    assert "yoetz recommend accept observation-enabled" in result.stdout
    assert "yoetz recommend decline observation-enabled" in result.stdout


def test_activation_accept_repreviews_and_applies_exact_digest(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    applied: list[tuple[IntegrationTarget, str]] = []
    executable = tmp_path / "codex-testing-bin"
    codex_home = tmp_path / "codex-testing"
    preview = SimpleNamespace(
        marketplace_bytes=b'{"plugins":[]}\n',
        config_toml_block='[plugins."yoetz@yoetz"]\nenabled = true\n',
        preview_digest="sha256:" + "a" * 64,
        codex_home=codex_home,
        plugin_install_path=codex_home / "plugins" / "cache" / "yoetz",
        plugin_source_digest="sha256:" + "b" * 64,
        plugin_install_digest="sha256:" + "c" * 64,
        executable_path=executable,
        executable_digest="sha256:" + "d" * 64,
        codex_version="0.148.0-alpha.6",
        probe_command=("--version",),
        inventory_command=("plugin", "list", "--marketplace", "yoetz", "--json"),
        install_command=("plugin", "add", "yoetz@yoetz", "--json"),
        probe_environment="temporary_owner_private_home",
        activation_environment=(
            ("CODEX_HOME", str(codex_home)),
            ("CODEX_TESTING_HOME", str(codex_home)),
        ),
        marketplace_preimage_digest="sha256:" + "e" * 64,
        config_preimage_digest="sha256:" + "f" * 64,
        cache_mutation_planned=True,
        inspection=SimpleNamespace(inventory_verified=False),
    )

    _patch_pending_context(monkeypatch, "codex-plugin-activation")

    def apply(
        target: IntegrationTarget,
        *,
        approved_digest: str,
        executable_path: str,
        codex_home: Path,
    ) -> None:
        assert executable_path == str(executable)
        del codex_home
        applied.append((target, approved_digest))

    def make_preview(
        _target: IntegrationTarget, *, executable_path: str, codex_home: Path
    ) -> object:
        assert executable_path == str(executable)
        del codex_home
        return preview

    def import_adapter(_name: str) -> object:
        return adapter

    adapter = SimpleNamespace(
        preview_activation=make_preview,
        apply_activation=apply,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend.importlib.import_module", import_adapter
    )

    result = _RUNNER.invoke(
        app,
        [
            "recommend",
            "accept",
            "codex-plugin-activation",
            "--codex-path",
            str(executable),
            "--codex-home",
            str(codex_home),
        ],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert preview.preview_digest in result.stdout
    assert '[plugins."yoetz@yoetz"]' in result.stdout
    assert str(codex_home) in result.stdout
    assert str(executable) in result.stdout
    assert preview.executable_digest in result.stdout
    assert preview.codex_version in result.stdout
    assert preview.plugin_source_digest in result.stdout
    assert preview.plugin_install_digest in result.stdout
    assert preview.marketplace_preimage_digest in result.stdout
    assert preview.config_preimage_digest in result.stdout
    assert f"{executable} --version" in result.stdout
    assert "Probe environment: temporary_owner_private_home" in result.stdout
    assert "Canonical plugin inventory verified before consent: no" in result.stdout
    assert f"{executable} plugin list --marketplace yoetz --json" in result.stdout
    assert f"{executable} plugin add yoetz@yoetz --json" in result.stdout
    assert f"CODEX_HOME={codex_home}" in result.stdout
    assert f"CODEX_TESTING_HOME={codex_home}" in result.stdout
    assert applied and applied[0][1] == preview.preview_digest
    assert "fresh Codex process/session" in result.stdout


def test_unknown_recommendation_is_rejected_without_creating_state(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)

    result = _RUNNER.invoke(app, ["recommend", "accept", "not-real"])

    assert result.exit_code == 2
    assert "recommendation_unknown" in result.output
    assert not (tmp_path / "recommendations.json").exists()


def test_known_but_not_pending_recommendation_is_rejected(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)

    async def satisfied(**_kwargs: object) -> RecommendationContext:
        return RecommendationContext(observation_enabled=True)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend._current_context", satisfied
    )

    result = _RUNNER.invoke(app, ["recommend", "accept", "observation-enabled"])

    assert result.exit_code == 2
    assert "recommendation_not_pending" in result.output


def test_observation_accept_fails_closed_on_concurrent_config_change(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    _patch_pending_context(monkeypatch, "observation-enabled")
    config_path = tmp_path / "config.toml"
    write_config_toml(YoetzConfig(observation=ObservationConfig(enabled=False)), path=config_path)
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend.config_file_path", lambda: config_path
    )

    def stale(*_args: object, **_kwargs: object) -> Path:
        raise ConfigError("config_preimage_mismatch")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend.write_config_toml_if_unchanged", stale
    )

    result = _RUNNER.invoke(app, ["recommend", "accept", "observation-enabled"])

    assert result.exit_code == 2
    assert "config_preview_stale" in result.output
    assert "observation-enabled" not in load_recommendation_state(root=tmp_path).decisions


def test_activation_preview_requires_explicit_post_preview_confirmation(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    _patch_pending_context(monkeypatch, "codex-plugin-activation")
    applied: list[str] = []
    executable = tmp_path / "codex-testing-bin"
    codex_home = tmp_path / "codex-testing"
    preview = SimpleNamespace(
        marketplace_bytes=b"{}\n",
        config_toml_block='[plugins."yoetz@yoetz"]\nenabled = true\n',
        preview_digest="sha256:" + "b" * 64,
        codex_home=codex_home,
        plugin_install_path=codex_home / "plugins" / "cache" / "yoetz",
        plugin_source_digest="sha256:" + "c" * 64,
        plugin_install_digest="sha256:" + "d" * 64,
        executable_path=executable,
        executable_digest="sha256:" + "e" * 64,
        codex_version="0.148.0-alpha.6",
        probe_command=("--version",),
        inventory_command=("plugin", "list", "--marketplace", "yoetz", "--json"),
        install_command=("plugin", "add", "yoetz@yoetz", "--json"),
        probe_environment="temporary_owner_private_home",
        activation_environment=(
            ("CODEX_HOME", str(codex_home)),
            ("CODEX_TESTING_HOME", str(codex_home)),
        ),
        marketplace_preimage_digest="sha256:" + "f" * 64,
        config_preimage_digest="sha256:" + "0" * 64,
        cache_mutation_planned=False,
        inspection=SimpleNamespace(inventory_verified=False),
    )

    def make_preview(
        _target: IntegrationTarget, *, executable_path: str, codex_home: Path
    ) -> object:
        assert executable_path == str(executable)
        del codex_home
        return preview

    def apply(
        _target: IntegrationTarget,
        *,
        approved_digest: str,
        executable_path: str,
        codex_home: Path,
    ) -> None:
        assert executable_path == str(executable)
        del codex_home
        applied.append(approved_digest)

    adapter = SimpleNamespace(preview_activation=make_preview, apply_activation=apply)

    def import_adapter(_name: str) -> object:
        return adapter

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend.importlib.import_module", import_adapter
    )

    result = _RUNNER.invoke(
        app,
        [
            "recommend",
            "accept",
            "codex-plugin-activation",
            "--codex-path",
            str(executable),
            "--codex-home",
            str(codex_home),
        ],
        input="n\n",
    )

    assert result.exit_code == 2
    assert preview.preview_digest in result.stdout
    assert "activation_not_approved" in result.output
    assert applied == []


def test_activation_accept_requires_exact_codex_path(tmp_path: Path, monkeypatch: object) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    _patch_pending_context(monkeypatch, "codex-plugin-activation")
    result = _RUNNER.invoke(
        app,
        [
            "recommend",
            "accept",
            "codex-plugin-activation",
            "--codex-home",
            str(tmp_path / "codex-testing"),
        ],
    )

    assert result.exit_code == 2
    assert "activation_codex_path_required" in result.output


def test_activation_accept_requires_exact_codex_home(tmp_path: Path, monkeypatch: object) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    _patch_pending_context(monkeypatch, "codex-plugin-activation")

    result = _RUNNER.invoke(
        app,
        [
            "recommend",
            "accept",
            "codex-plugin-activation",
            "--codex-path",
            str(tmp_path / "codex-testing-bin"),
        ],
    )

    assert result.exit_code == 2
    assert "activation_codex_home_required" in result.output


def _seed_package_update_pending(tmp_path: Path) -> None:
    """Daemon-style refresh: a real performed advisory establishes package-update pending."""

    advisory = build_package_update_advisory(
        installed_version=installed_package_version(),
        latest_version="9999.0.0",
        source="network",
    )
    state = anyio.run(
        lambda: refresh_pending(
            context=RecommendationContext(package_update=advisory), root=tmp_path
        )
    )
    assert "package-update" in state.pending


def _patch_config_path(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "yoetz.cli.recommend.config_file_path", lambda: tmp_path / "config.toml"
    )


def test_package_update_pending_survives_policyless_list_and_accepts(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Regression: the real policy-less CLI context must not erase daemon-set pending advice."""

    _patch_state_root(monkeypatch, tmp_path)
    _patch_config_path(monkeypatch, tmp_path)
    _seed_package_update_pending(tmp_path)

    listed = _RUNNER.invoke(app, ["recommend", "list"])

    assert listed.exit_code == 0, listed.output
    assert "package-update" in listed.stdout
    assert "package-update" in load_recommendation_state(root=tmp_path).pending

    accepted = _RUNNER.invoke(app, ["recommend", "accept", "package-update"])

    assert accepted.exit_code == 0, accepted.output
    assert "recommendation_not_pending" not in accepted.output
    assert "uv tool upgrade yoetz" in accepted.stdout
    state = load_recommendation_state(root=tmp_path)
    assert state.decisions["package-update"].decision == "accepted"
    assert "package-update" not in state.pending


def test_package_update_decline_succeeds_after_policyless_list(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    _patch_config_path(monkeypatch, tmp_path)
    _seed_package_update_pending(tmp_path)

    listed = _RUNNER.invoke(app, ["recommend", "list"])
    assert listed.exit_code == 0, listed.output

    declined = _RUNNER.invoke(app, ["recommend", "decline", "package-update"])

    assert declined.exit_code == 0, declined.output
    assert "will not be shown again" in declined.stdout
    state = load_recommendation_state(root=tmp_path)
    assert "package-update" not in state.pending
    assert state.decisions["package-update"].decision == "declined"


def test_activation_decline_needs_no_codex_authority_and_is_durable(
    tmp_path: Path, monkeypatch: object
) -> None:
    _patch_state_root(monkeypatch, tmp_path)
    store_recommendation_state(
        RecommendationState(
            last_evaluated_version=installed_package_version(),
            pending=("codex-plugin-activation",),
        ),
        root=tmp_path,
    )

    result = _RUNNER.invoke(app, ["recommend", "decline", "codex-plugin-activation"])

    assert result.exit_code == 0, result.output
    assert "will not be shown again" in result.stdout
    state = load_recommendation_state(root=tmp_path)
    assert state.pending == ()
    assert state.decisions["codex-plugin-activation"].decision == "declined"

    refreshed = anyio.run(
        lambda: refresh_pending(
            context=RecommendationContext(codex_activation_state="installed_not_activated"),
            root=tmp_path,
            force=True,
        )
    )

    assert "codex-plugin-activation" not in refreshed.pending
    assert refreshed.decisions["codex-plugin-activation"].decision == "declined"


def test_decline_requires_cached_pending(tmp_path: Path, monkeypatch: object) -> None:
    _patch_state_root(monkeypatch, tmp_path)

    result = _RUNNER.invoke(app, ["recommend", "decline", "codex-plugin-activation"])

    assert result.exit_code == 2
    assert "recommendation_not_pending" in result.output

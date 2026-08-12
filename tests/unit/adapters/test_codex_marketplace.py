"""Tests for consent-gated Codex marketplace activation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.codex_marketplace import (
    ActivationInspection,
    ActivationPreview,
    ActivationState,
    resolve_codex_home_for_binary,
)
from yoetz.adapters.integrations.codex_marketplace import (
    apply_activation as _apply_activation,
)
from yoetz.adapters.integrations.codex_marketplace import (
    inspect_activation as _inspect_activation,
)
from yoetz.adapters.integrations.codex_marketplace import (
    preview_activation as _preview_activation,
)
from yoetz.adapters.integrations.codex_plugin import install_plugin
from yoetz.ports.integrations import (
    IntegrationError,
    IntegrationReason,
    IntegrationScope,
    IntegrationTarget,
)


def _target(tmp_path: Path) -> tuple[IntegrationTarget, Path, Path]:
    project = tmp_path / "project"
    project.mkdir(mode=0o700)
    home = tmp_path / "codex"
    home.mkdir(mode=0o700)
    target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(project))
    return target, project, home


def _install(target: IntegrationTarget) -> None:
    install_plugin(target, allow_untested=True)


class _FakeCodex:
    def __init__(self, target: IntegrationTarget, home: Path) -> None:
        self.project = Path(target.project_root)
        self.home = home
        self.executable = home / "codex"
        self.executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.executable.chmod(0o700)
        self.calls: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []
        self.fail_add_after_copy = False

    def __call__(
        self, command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(command)
        environment_obj = kwargs.get("env")
        assert isinstance(environment_obj, dict)
        environment = cast(dict[str, str], environment_obj)
        self.environments.append(environment)
        args = tuple(command[1:])
        if args == ("--version",):
            return subprocess.CompletedProcess(
                command, 0, stdout=b"codex-cli 0.148.0-alpha.6\n", stderr=b""
            )
        elif args == ("plugin", "list", "--marketplace", "yoetz", "--json"):
            body: object
            config = self.home / "config.toml"
            cache = self.home / "plugins/cache/yoetz/yoetz/0.1.0"
            if not config.exists() or "marketplaces.yoetz" not in config.read_text(
                encoding="utf-8"
            ):
                body = {"installed": [], "available": []}
            else:
                installed = cache.is_dir()
                row = {
                    "pluginId": "yoetz@yoetz",
                    "name": "yoetz",
                    "marketplaceName": "yoetz",
                    "version": "0.1.0",
                    "installed": installed,
                    "enabled": True,
                    "source": {
                        "source": "local",
                        "path": str(self.project / ".agents/plugins/yoetz"),
                    },
                    "marketplaceSource": {
                        "sourceType": "local",
                        "source": str(self.project),
                    },
                }
                body = {"installed" if installed else "available": [row]}
                body["available" if installed else "installed"] = []
        elif args == ("plugin", "add", "yoetz@yoetz", "--json"):
            source = self.project / ".agents/plugins/yoetz"
            destination = self.home / "plugins/cache/yoetz/yoetz/0.1.0"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
            body = {
                "pluginId": "yoetz@yoetz",
                "name": "yoetz",
                "marketplaceName": "yoetz",
                "version": "0.1.0",
                "installedPath": str(destination),
            }
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(
            command,
            1
            if self.fail_add_after_copy and args == ("plugin", "add", "yoetz@yoetz", "--json")
            else 0,
            stdout=json.dumps(body).encode(),
            stderr=b"failed after copy" if self.fail_add_after_copy else b"",
        )


def _selected_home(codex_home: Path | str | None) -> Path:
    if codex_home is not None:
        return Path(codex_home)
    return Path(os.environ["CODEX_HOME"])


def preview_activation(
    target: IntegrationTarget, *, codex_home: Path | str | None = None
) -> ActivationPreview:
    home = _selected_home(codex_home)
    runner = _FakeCodex(target, home)
    return _preview_activation(
        target,
        executable_path=str(runner.executable),
        codex_home=home,
        _run=runner,
    )


def inspect_activation(
    target: IntegrationTarget, *, codex_home: Path | str | None = None
) -> ActivationInspection:
    home = _selected_home(codex_home)
    runner = _FakeCodex(target, home)
    return _inspect_activation(
        target,
        executable_path=str(runner.executable),
        codex_home=home,
        _run=runner,
    )


def apply_activation(
    target: IntegrationTarget,
    *,
    approved_digest: str,
    codex_home: Path | str | None = None,
) -> ActivationInspection:
    home = _selected_home(codex_home)
    runner = _FakeCodex(target, home)
    return _apply_activation(
        target,
        executable_path=str(runner.executable),
        approved_digest=approved_digest,
        codex_home=home,
        _run=runner,
    )


def test_inspect_preview_and_apply_activation(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    assert inspect_activation(target, codex_home=home).state is ActivationState.NOT_INSTALLED
    _install(target)
    assert (
        inspect_activation(target, codex_home=home).state is ActivationState.INSTALLED_NOT_ACTIVATED
    )

    preview = preview_activation(target, codex_home=home)
    document = json.loads(preview.marketplace_bytes)
    assert document["plugins"] == [
        {
            "name": "yoetz",
            "source": {"path": "./.agents/plugins/yoetz", "source": "local"},
        }
    ]
    assert f'source = "{project}"' in preview.config_toml_block
    assert '[plugins."yoetz@yoetz"]' in preview.config_toml_block
    assert preview.plugin_source_digest.startswith("sha256:")
    assert preview.codex_home == home
    assert preview.plugin_install_path == home / "plugins/cache/yoetz/yoetz/0.1.0"
    assert preview.plugin_install_digest.startswith("sha256:")
    assert preview.probe_command == ("--version",)
    assert preview.inventory_command == ("plugin", "list", "--marketplace", "yoetz", "--json")
    assert preview.install_command == ("plugin", "add", "yoetz@yoetz", "--json")

    result = apply_activation(
        target,
        codex_home=home,
        approved_digest=preview.preview_digest,
    )
    assert result == inspect_activation(target, codex_home=home)
    assert result.state is ActivationState.ACTIVE
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    assert config["marketplaces"]["yoetz"]["source"] == str(project)
    assert config["plugins"]["yoetz@yoetz"]["enabled"] is True


def test_apply_preserves_foreign_marketplace_entries_and_config(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    marketplace = project / ".agents/plugins/marketplace.json"
    marketplace.write_text(
        json.dumps(
            {
                "name": "yoetz",
                "interface": {"displayName": "Local tools"},
                "plugins": [
                    {
                        "name": "other",
                        "source": {"source": "local", "path": "./plugins/other"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (home / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
    preview = preview_activation(target, codex_home=home)
    apply_activation(target, codex_home=home, approved_digest=preview.preview_digest)
    document = json.loads(marketplace.read_bytes())
    assert document["interface"] == {"displayName": "Local tools"}
    assert [row["name"] for row in document["plugins"]] == ["other", "yoetz"]
    assert (home / "config.toml").read_text(encoding="utf-8").startswith('model = "gpt-5"\n')


def test_foreign_same_name_entry_is_refused(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    marketplace = project / ".agents/plugins/marketplace.json"
    marketplace.write_text(
        json.dumps(
            {
                "name": "yoetz",
                "plugins": [
                    {
                        "name": "yoetz",
                        "source": {"source": "local", "path": "./somewhere-else"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert inspect_activation(target, codex_home=home).state is ActivationState.FOREIGN
    with pytest.raises(IntegrationError) as caught:
        preview_activation(target, codex_home=home)
    assert caught.value.reason is IntegrationReason.DESTINATION_CONFLICT


def test_foreign_config_source_is_refused(tmp_path: Path) -> None:
    target, _project, home = _target(tmp_path)
    _install(target)
    (home / "config.toml").write_text(
        '[marketplaces.yoetz]\nsource_type = "local"\nsource = "/some/other/project"\n',
        encoding="utf-8",
    )
    assert inspect_activation(target, codex_home=home).state is ActivationState.FOREIGN
    with pytest.raises(IntegrationError) as caught:
        preview_activation(target, codex_home=home)
    assert caught.value.reason is IntegrationReason.DESTINATION_CONFLICT


def test_symlinked_managed_ancestor_and_leaf_are_refused(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".agents").symlink_to(outside, target_is_directory=True)
    with pytest.raises(IntegrationError) as caught:
        inspect_activation(target, codex_home=home)
    assert caught.value.reason is IntegrationReason.TARGET_UNSAFE

    (project / ".agents").unlink()
    _install(target)
    config_target = tmp_path / "config-target.toml"
    config_target.write_text("", encoding="utf-8")
    (home / "config.toml").symlink_to(config_target)
    with pytest.raises(IntegrationError) as caught:
        preview_activation(target, codex_home=home)
    assert caught.value.reason is IntegrationReason.TARGET_UNSAFE


def test_stale_digest_refuses_without_writing(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    preview = preview_activation(target, codex_home=home)
    config = home / "config.toml"
    config.write_text('model = "changed"\n', encoding="utf-8")
    before = config.read_bytes()
    with pytest.raises(IntegrationError) as caught:
        apply_activation(target, codex_home=home, approved_digest=preview.preview_digest)
    assert caught.value.reason is IntegrationReason.PREVIEW_STALE
    assert config.read_bytes() == before
    assert not (project / ".agents/plugins/marketplace.json").exists()


def test_second_file_stale_fence_preserves_approved_partial_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    preview = preview_activation(target, codex_home=home)
    config = home / "config.toml"
    real_assert = __import__(
        "yoetz.adapters.integrations.codex_marketplace", fromlist=["_assert_snapshot"]
    )._assert_snapshot

    def race(path: Path, expected: bytes | None) -> None:
        if path == config:
            config.write_text('model = "concurrent"\n', encoding="utf-8")
        real_assert(path, expected)

    monkeypatch.setattr("yoetz.adapters.integrations.codex_marketplace._assert_snapshot", race)
    with pytest.raises(IntegrationError) as caught:
        apply_activation(target, codex_home=home, approved_digest=preview.preview_digest)
    assert caught.value.reason is IntegrationReason.PREVIEW_STALE
    assert (project / ".agents/plugins/marketplace.json").read_bytes() == preview.marketplace_bytes
    assert config.read_text(encoding="utf-8") == 'model = "concurrent"\n'


def test_apply_refuses_before_writing_when_plugin_is_not_installed(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    preview = preview_activation(target, codex_home=home)
    with pytest.raises(IntegrationError) as caught:
        apply_activation(target, codex_home=home, approved_digest=preview.preview_digest)
    assert caught.value.reason is IntegrationReason.PARTIAL_INSTALL
    assert not (project / ".agents/plugins/marketplace.json").exists()
    assert not (home / "config.toml").exists()


def test_apply_refuses_modified_plugin_source_before_activation(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    preview = preview_activation(target, codex_home=home)
    (project / ".agents/plugins/yoetz/hooks/hooks.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(IntegrationError) as caught:
        apply_activation(target, codex_home=home, approved_digest=preview.preview_digest)
    assert caught.value.reason is IntegrationReason.PARTIAL_INSTALL
    assert not (project / ".agents/plugins/marketplace.json").exists()
    assert not (home / "config.toml").exists()


def test_preview_refuses_extra_plugin_source_file_before_any_install_command(
    tmp_path: Path,
) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    (project / ".agents/plugins/yoetz/credential123").write_text("not managed", encoding="utf-8")
    runner = _FakeCodex(target, home)
    with pytest.raises(IntegrationError) as caught:
        _preview_activation(
            target,
            executable_path=str(runner.executable),
            codex_home=home,
            _run=runner,
        )
    assert caught.value.reason is IntegrationReason.DESTINATION_CONFLICT
    assert not any(call[1:3] == ("plugin", "add") for call in runner.calls)


def test_add_failure_after_copy_preserves_honest_partial_state_for_retry(
    tmp_path: Path,
) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    runner = _FakeCodex(target, home)
    preview = _preview_activation(
        target,
        executable_path=str(runner.executable),
        codex_home=home,
        _run=runner,
    )
    runner.fail_add_after_copy = True
    with pytest.raises(IntegrationError) as caught:
        _apply_activation(
            target,
            executable_path=str(runner.executable),
            codex_home=home,
            approved_digest=preview.preview_digest,
            _run=runner,
        )
    assert caught.value.reason is IntegrationReason.WRITE_FAILED
    assert (home / "plugins/cache/yoetz/yoetz/0.1.0").exists()
    assert (project / ".agents/plugins/marketplace.json").read_bytes() == preview.marketplace_bytes
    assert (home / "config.toml").is_file()


def test_exact_commands_force_approved_home_and_version_uses_temporary_home(
    tmp_path: Path,
) -> None:
    target, _project, home = _target(tmp_path)
    _install(target)
    runner = _FakeCodex(target, home)
    preview = _preview_activation(
        target,
        executable_path=str(runner.executable),
        codex_home=home,
        _run=runner,
    )
    assert runner.calls and all(command[1:] == ("--version",) for command in runner.calls)
    for probe_environment in runner.environments:
        assert probe_environment["CODEX_HOME"] == probe_environment["CODEX_TESTING_HOME"]
        assert Path(probe_environment["CODEX_HOME"]) != home
        assert not Path(probe_environment["CODEX_HOME"]).exists()
    _apply_activation(
        target,
        executable_path=str(runner.executable),
        codex_home=home,
        approved_digest=preview.preview_digest,
        _run=runner,
    )
    for command, environment in zip(runner.calls, runner.environments, strict=True):
        assert environment["CODEX_HOME"] == environment["CODEX_TESTING_HOME"]
        if command[1:] != ("--version",):
            assert environment["CODEX_HOME"] == str(home)


def test_final_plugin_fence_preserves_approved_partial_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    preview = preview_activation(target, codex_home=home)
    hook_path = project / ".agents/plugins/yoetz/hooks/hooks.json"
    module = __import__(
        "yoetz.adapters.integrations.codex_marketplace", fromlist=["_assert_plugin_source"]
    )
    real_assert = module._assert_plugin_source
    calls = 0

    def mutate_at_final_fence(
        checked_target: IntegrationTarget,
        expected_digest: str,
        expected_members: object,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            hook_path.write_text("{}\n", encoding="utf-8")
        real_assert(checked_target, expected_digest, expected_members)

    monkeypatch.setattr(
        "yoetz.adapters.integrations.codex_marketplace._assert_plugin_source",
        mutate_at_final_fence,
    )
    with pytest.raises(IntegrationError) as caught:
        apply_activation(target, codex_home=home, approved_digest=preview.preview_digest)
    assert caught.value.reason is IntegrationReason.PARTIAL_INSTALL
    assert (project / ".agents/plugins/marketplace.json").read_bytes() == preview.marketplace_bytes
    assert (home / "config.toml").is_file()


def test_codex_home_environment_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, _project, home = _target(tmp_path)
    _install(target)
    monkeypatch.setenv("CODEX_HOME", str(home))
    preview = preview_activation(target)
    apply_activation(target, approved_digest=preview.preview_digest)
    assert (home / "config.toml").is_file()
    assert inspect_activation(target).state is ActivationState.ACTIVE


def test_resolve_explicit_codex_home_without_subprocess(tmp_path: Path) -> None:
    executable = tmp_path / "codex-testing"
    selected_home = tmp_path / "selected-home"
    selected_home.mkdir(mode=0o700)
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        raise AssertionError("home selection must not spawn")

    resolved = resolve_codex_home_for_binary(str(executable), codex_home=selected_home, _run=run)
    assert resolved == selected_home
    assert calls == []


def test_explicit_selected_home_never_mutates_ambient_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _project, selected_home = _target(tmp_path)
    _install(target)
    ambient = tmp_path / "ambient"
    ambient.mkdir(mode=0o700)
    ambient_config = ambient / "config.toml"
    ambient_config.write_text('model = "do-not-touch"\n', encoding="utf-8")
    before = ambient_config.read_bytes()
    monkeypatch.setenv("CODEX_HOME", str(ambient))
    preview = preview_activation(target, codex_home=selected_home)
    apply_activation(target, codex_home=selected_home, approved_digest=preview.preview_digest)
    assert ambient_config.read_bytes() == before
    assert inspect_activation(target, codex_home=selected_home).state is ActivationState.ACTIVE


def test_missing_explicit_home_is_refused_without_touching_ambient_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _project, selected_home = _target(tmp_path)
    _install(target)
    ambient = tmp_path / "ambient"
    ambient.mkdir(mode=0o700)
    ambient_config = ambient / "config.toml"
    ambient_config.write_text('model = "do-not-touch"\n', encoding="utf-8")
    before = ambient_config.read_bytes()
    monkeypatch.setenv("CODEX_HOME", str(ambient))
    monkeypatch.setenv("CODEX_TESTING_HOME", str(selected_home))
    runner = _FakeCodex(target, selected_home)
    testing_executable = runner.executable.with_name("codex-testing")
    runner.executable.rename(testing_executable)
    runner.executable = testing_executable
    with pytest.raises(IntegrationError) as caught:
        _preview_activation(
            target,
            executable_path=str(runner.executable),
            _run=runner,
        )
    assert caught.value.reason is IntegrationReason.TARGET_UNTRUSTED
    assert ambient_config.read_bytes() == before
    assert not (selected_home / "config.toml").is_file()


def test_approved_digest_is_bound_to_exact_codex_home(tmp_path: Path) -> None:
    target, project, first_home = _target(tmp_path)
    _install(target)
    second_home = tmp_path / "second-home"
    second_home.mkdir(mode=0o700)
    preview = preview_activation(target, codex_home=first_home)
    with pytest.raises(IntegrationError) as caught:
        apply_activation(target, codex_home=second_home, approved_digest=preview.preview_digest)
    assert caught.value.reason is IntegrationReason.PREVIEW_STALE
    assert not (project / ".agents/plugins/marketplace.json").exists()
    assert not (first_home / "config.toml").exists()
    assert not (second_home / "config.toml").exists()


def test_disabled_owner_authored_plugin_table_is_not_rewritten(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    (home / "config.toml").write_text(
        "[marketplaces.yoetz]\n"
        'source_type = "local"\n'
        f'source = "{project}"\n\n'
        '[plugins."yoetz@yoetz"]\n'
        "enabled = false # owner explicitly accepts re-enable\n",
        encoding="utf-8",
    )
    before = (home / "config.toml").read_bytes()
    with pytest.raises(IntegrationError) as caught:
        preview_activation(target, codex_home=home)
    assert caught.value.reason is IntegrationReason.DESTINATION_CONFLICT
    assert (home / "config.toml").read_bytes() == before


def test_unrelated_personal_marketplace_is_not_foreign(tmp_path: Path) -> None:
    target, _project, home = _target(tmp_path)
    _install(target)
    personal = home / ".agents/plugins/marketplace.json"
    personal.parent.mkdir(parents=True)
    personal.write_text(
        json.dumps(
            {
                "name": "personal",
                "plugins": [
                    {
                        "name": "other",
                        "source": {"source": "local", "path": "./plugins/other"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert (
        inspect_activation(target, codex_home=home).state is ActivationState.INSTALLED_NOT_ACTIVATED
    )


def test_idempotent_reapply_keeps_exact_bytes(tmp_path: Path) -> None:
    target, project, home = _target(tmp_path)
    _install(target)
    first = preview_activation(target, codex_home=home)
    apply_activation(target, codex_home=home, approved_digest=first.preview_digest)
    marketplace = project / ".agents/plugins/marketplace.json"
    config = home / "config.toml"
    before = (marketplace.read_bytes(), config.read_bytes())
    second = preview_activation(target, codex_home=home)
    apply_activation(target, codex_home=home, approved_digest=second.preview_digest)
    assert (marketplace.read_bytes(), config.read_bytes()) == before
    assert os.stat(config).st_mode & 0o777 == 0o600

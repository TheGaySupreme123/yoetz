"""CLI coverage for ``yoetz provider endpoint`` selectors and interactive gating.

Pins the interactive-picker predicate so ``--grok`` matches ``--fireworks``
shape, mutual exclusion across shorthand/provider/origin combinations, alias
resolution for Grok/xAI, and interactive menu choice 6.
"""

from __future__ import annotations

import contextlib
import re
import sys
import tomllib
from collections.abc import Generator, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import yoetz.cli.app as cli
import yoetz.cli.provider_binding as provider_binding
import yoetz.config.write as write_module
from yoetz.config.write import grok_provider, provider_preset

_RUNNER = CliRunner()
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


@pytest.fixture
def endpoint_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect service-owned config.toml writes into the test sandbox."""

    target = tmp_path / "config.toml"

    def _config_path(*, _probe: object | None = None) -> Path:
        del _probe
        return target

    def _ensure(path: Path, *, _probe: object | None = None) -> Path:
        del _probe
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(write_module, "config_file_path", _config_path)
    monkeypatch.setattr(write_module, "ensure_owner_only_dir", _ensure)
    monkeypatch.setattr(provider_binding, "config_file_path", _config_path)
    return target


@pytest.fixture
def tty_streams(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make CliRunner isolation report an interactive TTY for stdin/stdout."""

    import typer.testing as typer_testing

    original = typer_testing.CliRunner.isolation

    @contextlib.contextmanager
    def isolation_tty(
        self: CliRunner,
        input: str | bytes | None = None,
        env: Mapping[str, str | None] | None = None,
        color: bool = False,
    ) -> Generator[Any]:
        with original(self, input=input, env=env, color=color) as streams:
            monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
            monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
            yield streams

    monkeypatch.setattr(typer_testing.CliRunner, "isolation", isolation_tty)
    yield


def test_grok_without_model_on_tty_matches_fireworks_not_generic_picker(
    tty_streams: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--grok`` alone must not re-open the provider menu (issue #44)."""

    prompt_calls: list[str] = []

    def fake_prompt(*, path: Path | None = None) -> Path | None:
        del path
        prompt_calls.append("prompt")
        return None

    monkeypatch.setattr(provider_binding, "prompt_provider_endpoint_binding", fake_prompt)

    fireworks = _RUNNER.invoke(cli.app, ["provider", "endpoint", "--fireworks"])
    grok = _RUNNER.invoke(cli.app, ["provider", "endpoint", "--grok"])

    assert fireworks.exit_code == 2
    assert grok.exit_code == 2
    assert "invalid_request" in _plain(fireworks.output)
    assert "invalid_request" in _plain(grok.output)
    assert prompt_calls == []
    assert "LLM endpoint" not in _plain(fireworks.output)
    assert "LLM endpoint" not in _plain(grok.output)


def test_bare_interactive_endpoint_enters_generic_picker(
    tty_streams: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no selector, a TTY still opens the reviewed-provider menu."""

    prompt_calls: list[str] = []

    def fake_prompt(*, path: Path | None = None) -> Path | None:
        del path
        prompt_calls.append("prompt")
        return None

    monkeypatch.setattr(provider_binding, "prompt_provider_endpoint_binding", fake_prompt)

    result = _RUNNER.invoke(cli.app, ["provider", "endpoint"])
    assert result.exit_code == 0
    assert prompt_calls == ["prompt"]


def test_non_tty_bare_endpoint_is_usage_failure() -> None:
    """Non-interactive streams keep the usage-failure path (no menu)."""

    result = _RUNNER.invoke(cli.app, ["provider", "endpoint"])
    assert result.exit_code == 2
    assert "invalid_request" in _plain(result.output)


def test_non_tty_grok_without_model_is_usage_failure() -> None:
    result = _RUNNER.invoke(cli.app, ["provider", "endpoint", "--grok"])
    assert result.exit_code == 2
    assert "invalid_request" in _plain(result.output)


@pytest.mark.parametrize(
    "args",
    [
        ["--official", "--fireworks", "--model", "m"],
        ["--official", "--grok", "--model", "m"],
        ["--fireworks", "--grok", "--model", "m"],
        ["--official", "--provider", "openai", "--model", "m"],
        ["--fireworks", "--provider", "fireworks", "--model", "m"],
        ["--grok", "--provider", "grok", "--model", "m"],
        ["--official", "--https-origin", "https://llm.example.com", "--model", "m"],
        ["--fireworks", "--https-origin", "https://llm.example.com", "--model", "m"],
        ["--grok", "--https-origin", "https://llm.example.com", "--model", "m"],
        [
            "--provider",
            "openai",
            "--https-origin",
            "https://llm.example.com",
            "--model",
            "m",
        ],
    ],
)
def test_selector_mutual_exclusion_matrix(args: list[str]) -> None:
    """Shorthands, ``--provider``, and ``--https-origin`` are pairwise exclusive."""

    result = _RUNNER.invoke(cli.app, ["provider", "endpoint", *args])
    assert result.exit_code == 2
    assert "invalid_request" in _plain(result.output)


@pytest.mark.parametrize("alias", ["grok", "xai", "x-ai"])
def test_provider_aliases_resolve_to_same_grok_preset(endpoint_config: Path, alias: str) -> None:
    result = _RUNNER.invoke(
        cli.app,
        [
            "provider",
            "endpoint",
            "--provider",
            alias,
            "--model",
            "grok-4.5",
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert endpoint_config.is_file()
    loaded = tomllib.loads(endpoint_config.read_text(encoding="utf-8"))
    provider = loaded["provider"]
    expected = grok_provider(model="grok-4.5")
    assert provider["provider_id"] == expected.provider_id
    assert provider["endpoint_profile_id"] == expected.endpoint_profile_id
    assert provider["endpoint_profile_version"] == expected.endpoint_profile_version
    assert provider["model"] == "grok-4.5"
    assert provider["capability_profile"] == expected.capability_profile
    assert provider_preset(alias).choice == "grok"


def test_grok_shorthand_writes_same_preset_as_provider_alias(
    endpoint_config: Path,
) -> None:
    result = _RUNNER.invoke(
        cli.app,
        ["provider", "endpoint", "--grok", "--model", "grok-4.5", "--json"],
    )
    assert result.exit_code == 0
    loaded = tomllib.loads(endpoint_config.read_text(encoding="utf-8"))
    assert loaded["provider"]["provider_id"] == "xai"
    assert loaded["provider"]["endpoint_profile_id"] == "xai-openai-chat-completions"
    assert loaded["provider"]["model"] == "grok-4.5"


def test_interactive_menu_choice_6_writes_grok_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Menu option 6 is Grok/xAI and must write the reviewed nonsecret preset."""

    target = tmp_path / "config.toml"
    prompts: list[str] = []
    echoes: list[str] = []

    def fake_prompt(text: str, default: object = ..., **_kwargs: object) -> str:
        prompts.append(text)
        if text == "Select":
            return "6"
        if "Model id" in text:
            # Accept the bundled default when the menu offers one.
            if default is not ... and default is not None:
                return str(default)
            return "grok-4.5"
        raise AssertionError(f"unexpected prompt: {text!r}")

    def fake_echo(msg: object = "", **_kwargs: object) -> None:
        del _kwargs
        echoes.append(str(msg))

    monkeypatch.setattr(provider_binding.typer, "prompt", fake_prompt)
    monkeypatch.setattr(provider_binding.typer, "echo", fake_echo)

    written = provider_binding.prompt_provider_endpoint_binding(path=target)
    assert written == target
    assert target.is_file()
    assert any(p == "Select" for p in prompts)
    loaded = tomllib.loads(target.read_text(encoding="utf-8"))
    assert loaded["provider"]["provider_id"] == "xai"
    assert loaded["provider"]["endpoint_profile_id"] == "xai-openai-chat-completions"
    assert loaded["provider"]["model"] == provider_preset("grok").default_model
    joined = "\n".join(echoes)
    assert "xai" in joined
    assert "xai-openai-chat-completions" in joined

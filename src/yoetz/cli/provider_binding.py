"""Interactive nonsecret LLM endpoint binding (Official OpenAI vs owner-declared).

Writes service-owned ``config.toml`` only. Credentials remain on the confidential
``yoetz provider credential`` ceremony path.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final, Literal

import typer

from yoetz.config.models import ConfigError, ProviderProfileConfig, YoetzConfig
from yoetz.config.paths import config_file_path
from yoetz.config.write import (
    fireworks_provider,
    official_openai_provider,
    owner_declared_openai_provider,
    write_provider_binding,
)

__all__ = [
    "NEXT_CREDENTIAL",
    "ProviderEndpointChoice",
    "apply_provider_endpoint_choice",
    "prompt_provider_endpoint_binding",
]

ProviderEndpointChoice = Literal["official_openai", "fireworks", "owner_declared"]

NEXT_CREDENTIAL: Final = (
    "run 'yoetz provider credential set' from a local terminal to provision the "
    "provider credential through the confidential ceremony"
)


def _load_base(path: Path | None) -> YoetzConfig:
    target = config_file_path() if path is None else path
    if not target.is_file():
        return YoetzConfig()
    try:
        raw = tomllib.loads(target.read_bytes().decode("utf-8"))
        return YoetzConfig.model_validate(raw, strict=True)
    except ConfigError, OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError:
        return YoetzConfig()


def apply_provider_endpoint_choice(
    choice: ProviderEndpointChoice,
    *,
    model: str,
    https_origin: str | None = None,
    path: Path | None = None,
) -> tuple[Path, ProviderProfileConfig]:
    """Validate and write the selected nonsecret provider binding."""

    if not model:
        raise ConfigError("config_value_invalid")
    if choice == "official_openai":
        provider = official_openai_provider(model=model)
    elif choice == "fireworks":
        provider = fireworks_provider(model=model)
    else:
        if https_origin is None:
            raise ConfigError("https_origin_invalid")
        provider = owner_declared_openai_provider(model=model, https_origin=https_origin)
    written = write_provider_binding(provider, path=path, base=_load_base(path))
    return written, provider


def prompt_provider_endpoint_binding(*, path: Path | None = None) -> Path | None:
    """TTY prompt: Official OpenAI vs custom HTTPS origin+model; never asks for secrets."""

    typer.echo("")
    typer.echo("LLM endpoint (nonsecret)")
    typer.echo("  1  Official OpenAI (api.openai.com)")
    typer.echo("  2  Fireworks AI (api.fireworks.ai/inference/v1)")
    typer.echo("  3  Custom OpenAI-compatible HTTPS origin")
    typer.echo("  s  Skip for now")
    raw = typer.prompt("Select", default="s").strip().lower()
    if raw in {"s", "skip", ""}:
        return None
    if raw not in {"1", "2", "3"}:
        typer.echo("invalid_request: choose 1, 2, 3, or s", err=True)
        return None

    model = typer.prompt("  Model id").strip()
    try:
        if raw == "1":
            written, provider = apply_provider_endpoint_choice(
                "official_openai", model=model, path=path
            )
        elif raw == "2":
            written, provider = apply_provider_endpoint_choice("fireworks", model=model, path=path)
        else:
            origin = typer.prompt(
                "  HTTPS origin (https://host[:port] only; no path, userinfo, or secrets)"
            ).strip()
            written, provider = apply_provider_endpoint_choice(
                "owner_declared", model=model, https_origin=origin, path=path
            )
    except ConfigError as error:
        typer.echo(f"invalid_request: {error.reason_code}", err=True)
        return None

    typer.echo(f"  wrote provider binding to {written}")
    typer.echo(
        f"  {provider.provider_id} / {provider.endpoint_profile_id}@"
        f"{provider.endpoint_profile_version} model={provider.model}"
    )
    if provider.owner_declared_endpoint is not None:
        typer.echo(f"  https_origin={provider.owner_declared_endpoint.https_origin}")
        typer.echo(
            "  note: owner-declared hosts use unknown data-use posture and never inherit "
            "the assisted recommendation badge"
        )
    typer.echo(f"  next: {NEXT_CREDENTIAL}")
    return written

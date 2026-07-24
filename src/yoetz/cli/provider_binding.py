"""Interactive nonsecret LLM endpoint binding for reviewed provider presets.

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
    anthropic_provider,
    fireworks_provider,
    google_gemini_provider,
    official_openai_provider,
    openrouter_provider,
    owner_declared_openai_provider,
    provider_preset,
    vercel_ai_gateway_provider,
    write_provider_binding,
)

__all__ = [
    "NEXT_CREDENTIAL",
    "ProviderEndpointChoice",
    "apply_provider_endpoint_choice",
    "prompt_provider_endpoint_binding",
]

ProviderEndpointChoice = Literal[
    "official_openai",
    "fireworks",
    "anthropic",
    "google_gemini",
    "openrouter",
    "vercel_ai_gateway",
    "owner_declared",
]

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
    elif choice == "anthropic":
        provider = anthropic_provider(model=model)
    elif choice == "google_gemini":
        provider = google_gemini_provider(model=model)
    elif choice == "openrouter":
        provider = openrouter_provider(model=model)
    elif choice == "vercel_ai_gateway":
        provider = vercel_ai_gateway_provider(model=model)
    else:
        if https_origin is None:
            raise ConfigError("https_origin_invalid")
        provider = owner_declared_openai_provider(model=model, https_origin=https_origin)
    written = write_provider_binding(provider, path=path, base=_load_base(path))
    return written, provider


def prompt_provider_endpoint_binding(*, path: Path | None = None) -> Path | None:
    """Prompt for a reviewed provider preset or custom origin; never asks for secrets."""

    typer.echo("")
    typer.echo("LLM endpoint (nonsecret)")
    typer.echo("  1  Official OpenAI (api.openai.com)")
    typer.echo("  2  Fireworks AI (api.fireworks.ai/inference/v1)")
    typer.echo("  3  Anthropic Claude (OpenAI-compatible Chat Completions)")
    typer.echo("  4  Google Gemini (OpenAI-compatible Chat Completions)")
    typer.echo("  5  OpenRouter (OpenAI-compatible Chat Completions)")
    typer.echo("  6  Vercel AI Gateway (OpenAI-compatible Responses)")
    typer.echo("  7  Custom OpenAI-compatible HTTPS origin")
    typer.echo("  s  Skip for now")
    raw = typer.prompt("Select", default="s").strip().lower()
    if raw in {"s", "skip", ""}:
        return None
    if raw not in {"1", "2", "3", "4", "5", "6", "7"}:
        typer.echo("invalid_request: choose 1, 2, 3, 4, 5, 6, 7, or s", err=True)
        return None

    choices: dict[str, ProviderEndpointChoice] = {
        "1": "official_openai",
        "2": "fireworks",
        "3": "anthropic",
        "4": "google_gemini",
        "5": "openrouter",
        "6": "vercel_ai_gateway",
        "7": "owner_declared",
    }
    choice = choices[raw]
    preset = None if choice == "owner_declared" else provider_preset(choice)
    model = typer.prompt(
        "  Model id",
        default=None if preset is None else preset.default_model,
        show_default=preset is not None,
    ).strip()
    try:
        if choice == "owner_declared":
            origin = typer.prompt(
                "  HTTPS origin (https://host[:port] only; no path, userinfo, or secrets)"
            ).strip()
            written, provider = apply_provider_endpoint_choice(
                "owner_declared", model=model, https_origin=origin, path=path
            )
        else:
            written, provider = apply_provider_endpoint_choice(choice, model=model, path=path)
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

"""Human-readable upgrade plan, with an explicitly accepted package-only action."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence

import typer

from yoetz.application.upgrade import HOSTS, build_upgrade_plan, execute_package_upgrade


def run_upgrade(
    *, hosts: Sequence[str] | None, options: Mapping[str, str], accept: bool, writers_stopped: bool
) -> int:
    try:
        steps = build_upgrade_plan(hosts or HOSTS, options)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        return 2
    typer.echo("Yoetz upgrade — preserve existing configuration")
    for step in steps:
        typer.echo(f"\n{step.title}\n{step.detail}")
        if step.cwd:
            typer.echo(f"Working directory: {shlex.quote(step.cwd)}")
        for argv in step.commands:
            command = (
                ("env", *(f"{key}={value}" for key, value in step.environment), *argv)
                if step.environment
                else argv
            )
            typer.echo("  " + shlex.join(command))
    if not accept:
        typer.echo("\nPlan only. No package, host, service, data, or setting was changed.")
        return 0
    if not writers_stopped:
        typer.echo(
            "upgrade_writers_must_be_stopped: quiesce old hosts/hooks and service, then pass --writers-stopped",
            err=True,
        )
        return 2
    try:
        outcome = execute_package_upgrade()
    except OSError, ValueError:
        outcome = "runtime_identity_unavailable"
    typer.echo(f"\nPackage step: {outcome}")
    if outcome != "package_command_succeeded":
        typer.echo("No full upgrade is claimed. Resolve the package step before continuing.")
        return 2
    typer.echo(
        "Host refresh, migration and activation remain unverified. Re-run yoetz upgrade from "
        "the fresh launcher with the same target options and follow the remaining stages. "
        "Do not repeat --accept merely to continue."
    )
    return 0

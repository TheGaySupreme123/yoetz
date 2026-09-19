"""Bounded package upgrade and explicit existing-host continuations.

Planning is read-only. Package execution never claims host activation or migration success.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from yoetz.adapters.package_upgrade import PACKAGE_UPGRADE_ARGV

HOSTS: Final = ("codex", "claude", "cursor")
_PATH_OPTIONS: Final = frozenset(
    {
        "project-root",
        "codex-path",
        "codex-home",
        "claude-path",
        "claude-config-root",
        "cache-root",
        "marketplace-root",
        "cursor-config-root",
    }
)
_HOST_OPTIONS: Final = {
    "claude": (
        "project-root",
        "claude-path",
        "claude-config-root",
        "cache-root",
        "marketplace-root",
    ),
    "cursor": ("project-root", "cursor-config-root"),
    "codex": ("project-root", "codex-path", "codex-home"),
}


@dataclass(frozen=True)
class UpgradeStep:
    title: str
    detail: str
    commands: tuple[tuple[str, ...], ...] = ()
    cwd: str | None = None
    environment: tuple[tuple[str, str], ...] = ()


def build_upgrade_plan(
    hosts: Sequence[str], options: Mapping[str, str], *, launcher: str = "yoetz"
) -> tuple[UpgradeStep, ...]:
    """Return only safe inspection/preview commands; apply uses their fresh returned authority."""
    if not hosts or len(set(hosts)) != len(hosts) or any(host not in HOSTS for host in hosts):
        raise ValueError("upgrade_host_invalid")
    if set(options) - (_PATH_OPTIONS | {"mcp-ownership", "route-profile", "observation-profile"}):
        raise ValueError("upgrade_option_invalid")
    for key, value in options.items():
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("upgrade_option_invalid")
        if key in _PATH_OPTIONS and not Path(value).is_absolute():
            raise ValueError("upgrade_target_must_be_absolute")
    ownership = options.get("mcp-ownership")
    route = options.get("route-profile")
    profile = options.get("observation-profile")
    if ownership not in {None, "external-registration", "plugin-managed"}:
        raise ValueError("upgrade_ownership_invalid")
    if route not in {None, "strict", "policy"}:
        raise ValueError("upgrade_route_invalid")
    if profile not in {None, "structural", "ordinary"}:
        raise ValueError("upgrade_observation_profile_invalid")
    if ownership == "external-registration" and route is not None:
        raise ValueError("upgrade_route_requires_plugin_managed")
    steps = [
        UpgradeStep(
            "Before package replacement",
            "Keep current settings and read the target release's migration notes. When old/new "
            "writers cannot coexist, quit the affected hosts and stop the service through its "
            "supported lifecycle before replacing the package. Never kill processes by pattern.",
            ((launcher, "version", "--json"), (launcher, "service", "status", "--json")),
        ),
        UpgradeStep(
            "Replace the package",
            "Run yoetz upgrade --accept --writers-stopped only for this uv-tool installation. A successful package "
            "command still requires a fresh launcher, host refresh, and any required migration.",
            (PACKAGE_UPGRADE_ARGV,),
        ),
        UpgradeStep(
            "Data and service",
            "Use the fresh launcher. An incompatible holder can be superseded by the ordinary "
            "service handshake; use service restart only when its status/repair asks for it. "
            "On unlock, supported existing task ledgers upgrade automatically with a verified "
            "backup before new writes are admitted. Existing tasks, settings and permissions "
            "are retained. If interrupted, retry the ordinary service startup; it resumes the "
            "recorded upgrade. Check service status for recovery guidance before continuing.",
            ((launcher, "service", "status", "--json"),),
        ),
    ]
    for host in hosts:
        required = list(_HOST_OPTIONS[host])
        if host != "codex":
            required += ["mcp-ownership", "observation-profile"]
            if ownership == "plugin-managed":
                required.append("route-profile")
        missing = [key for key in required if key not in options]
        if missing:
            steps.append(
                UpgradeStep(
                    f"{host}: select existing target",
                    "Preserve existing values; provide "
                    + ", ".join(f"--{key}" for key in missing)
                    + ". Inspect the existing registration before choosing any value. No target is inferred from ambient homes.",
                )
            )
            continue
        project = options["project-root"]
        if host == "codex":
            steps.append(
                UpgradeStep(
                    "codex: refresh installed guidance and activation",
                    "Preview and refresh the existing skill, then re-evaluate the exact plugin "
                    "target. Accept only its fresh activation preview using the returned command. "
                    "Inspect the existing MCP registration; if stale, preview/reapply its exact "
                    "route through integrate codex mcp. Do not rerun general privacy setup.",
                    (
                        (launcher, "integrate", "codex", "skill", "preview", "--json"),
                        (
                            launcher,
                            "integrate",
                            "codex",
                            "mcp",
                            "status",
                            "--codex-path",
                            options["codex-path"],
                            "--json",
                        ),
                        (
                            launcher,
                            "recommend",
                            "list",
                            "--codex-path",
                            options["codex-path"],
                            "--codex-home",
                            options["codex-home"],
                        ),
                    ),
                    cwd=project,
                    environment=(
                        ("CODEX_HOME", options["codex-home"]),
                        ("CODEX_TESTING_HOME", options["codex-home"]),
                    ),
                )
            )
        else:
            args = tuple(item for key in _HOST_OPTIONS[host] for item in (f"--{key}", options[key]))
            args += (
                "--format",
                "native",
                "--mcp-ownership",
                options["mcp-ownership"],
                "--observation-profile",
                options["observation-profile"],
            )
            if route is not None:
                args += ("--route-profile", route)
            prefix = (launcher, "integrate", host, "plugin")
            action = "update" if host == "claude" else "replace"
            apply = "update" if host == "claude" else "install"
            steps.append(
                UpgradeStep(
                    f"{host}: refresh installed plugin",
                    f"Inspect status, review the {action} preview, follow its authorization "
                    f"continuation, then use plugin {apply} with the same roots, request id, "
                    "preview digest, ownership, route and observation profile. Recheck status. "
                    "A modified or foreign artifact needs manual review, never forced replacement. "
                    "For a portable/development carrier, use its original installation path instead of this native plan.",
                    (
                        prefix + ("status",) + args + ("--json",),
                        prefix + ("preview",) + args + ("--action", action, "--json"),
                    ),
                    cwd=project,
                )
            )
    steps.append(
        UpgradeStep(
            "Activate and verify",
            "Reload/start a fresh Claude session; fully quit/relaunch Codex or Cursor when runtime "
            "status requires it. Verify the fresh package version, service identity, each selected "
            "host's artifact/runtime status and the service's completed data upgrade before reporting completion. "
            "Offer new settings separately; upgrading never opts into Expanded review or new disclosure.",
            ((launcher, "version", "--json"), (launcher, "service", "status", "--json")),
        )
    )
    return tuple(steps)

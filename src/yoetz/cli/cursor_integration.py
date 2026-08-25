"""Explicit local Cursor plugin lifecycle commands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, cast

from yoetz.adapters.integrations.cursor_integration import (
    CursorIntegrationError,
    CursorPluginArtifact,
    CursorPluginStatus,
    CursorPluginTarget,
    apply_cursor_plugin,
    preview_cursor_plugin,
    remove_cursor_plugin,
    render_cursor_plugin,
    status_cursor_plugin,
)
from yoetz.adapters.integrations.macos_artifact_presence import MacOSArtifactUserPresence
from yoetz.adapters.integrations.portable_plugin import (
    ArtifactUserPresencePort,
    ElevatedPortableArtifactReview,
)
from yoetz.domain.values import RequestId, request_id
from yoetz.ports.plugin_artifacts import (
    ArtifactAuthority,
    McpOwnership,
    PluginArtifactAction,
    PluginFormatProfile,
    PluginMutationReviewPort,
)
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.ids import IdKind, new_id

__all__ = ["run_cursor_plugin_command"]


def _artifact_authority(
    accepted_preview_digest: str, *, state: Path | None
) -> ArtifactAuthority | None:
    """Bind the exact ``plugin_artifact_apply`` pending review to the accepted preview digest.

    Returning ``None`` when no matching pending exists keeps the refusal inside the adapter,
    which reconciles an already-committed replay before it asks for authority at all.
    """

    from yoetz.service.elevated_bootstrap import ElevatedBootstrapError, load_pending

    try:
        pending = load_pending(_state=state)
    except ElevatedBootstrapError:
        return None
    if (
        pending is None
        or pending.operation != "plugin_artifact_apply"
        or pending.target_digest != accepted_preview_digest
    ):
        return None
    return ArtifactAuthority("review_only", accepted_preview_digest, pending.pending_id)


def _emit(value: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        sys.stdout.buffer.write(canonical_encode(cast(JsonValue, value)) + b"\n")
        return
    for key, item in value.items():
        rendered = (
            canonical_encode(cast(JsonValue, item)).decode("utf-8")
            if isinstance(item, (dict, list, tuple))
            else str(item)
        )
        sys.stdout.write(f"{key}: {rendered}\n")


def _artifact(
    format_name: str, ownership_name: str, route_name: str | None
) -> CursorPluginArtifact:
    formats = {
        "native": PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        "portable": PluginFormatProfile.AGENT_PLUGINS_1,
    }
    ownerships = {
        "external-registration": McpOwnership.EXTERNAL_REGISTRATION,
        "plugin-managed": McpOwnership.PLUGIN_MANAGED,
    }
    try:
        format_profile = formats[format_name]
        ownership = ownerships[ownership_name]
    except KeyError as exc:
        raise ValueError("cursor_plugin_option_invalid") from exc
    route: Literal["strict", "policy"] | None
    if route_name is None:
        route = None
    elif route_name in {"strict", "policy"}:
        route = cast(Literal["strict", "policy"], route_name)
    else:
        raise ValueError("cursor_mcp_route_invalid")
    if (ownership is McpOwnership.PLUGIN_MANAGED) != (route is not None):
        raise ValueError(
            "cursor_mcp_route_required"
            if ownership is McpOwnership.PLUGIN_MANAGED
            else "cursor_mcp_route_forbidden"
        )
    return render_cursor_plugin(format_profile, mcp_ownership=ownership, route_profile=route)


def _request(value: str | None) -> RequestId:
    return request_id(new_id(IdKind.REQUEST) if value is None else value)


def _status_body(status: CursorPluginStatus) -> dict[str, object]:
    return {
        "artifact_digest": status.artifact_digest,
        "format_profile": None if status.format_profile is None else status.format_profile.value,
        "installed_digest": status.installed_digest,
        "marker_valid": status.marker_valid,
        "mcp": {
            "observed": status.mcp_observation.observed,
            "ownership_state": status.mcp_observation.ownership_state.value,
            "present_sources": [item.value for item in status.mcp_observation.present_sources],
            "route_profile": status.mcp_observation.route_profile,
            "winning_source": (
                None
                if status.mcp_observation.winning_source is None
                else status.mcp_observation.winning_source.value
            ),
        },
        "operation_state": status.operation_state.value,
        "proof": {item.facet.value: item.status for item in status.proof},
        "rollback_available": status.rollback_available,
        "scope": "user",
        "state": status.state.value,
    }


def run_cursor_plugin_command(
    command: str,
    *,
    harness: str,
    cursor_config_root: Path,
    project_root: Path | None,
    format_name: str,
    ownership_name: str,
    route_profile: str | None,
    requested_action: str | None,
    request_value: str | None,
    preview_digest: str | None,
    accept: bool,
    json_output: bool,
    _state: Path | None = None,
    _presence: ArtifactUserPresencePort | None = None,
) -> int:
    """Run one path-explicit Cursor operation without reading ambient Cursor state."""

    if harness != "cursor" or command not in {"preview", "install", "status", "remove"}:
        sys.stderr.write("cursor_plugin_command_invalid\n")
        return 2
    try:
        artifact = _artifact(format_name, ownership_name, route_profile)
        target = CursorPluginTarget(str(cursor_config_root.expanduser().absolute()))
        project = None if project_root is None else project_root.expanduser().absolute()
        status = status_cursor_plugin(target, artifact, project_root=project)
        if command == "status":
            _emit(_status_body(status), json_output=json_output)
            return 0

        request = _request(request_value)
        if command == "remove":
            action = PluginArtifactAction.REMOVE
        elif requested_action is None:
            action = (
                PluginArtifactAction.INSTALL
                if status.state.value == "absent"
                else PluginArtifactAction.REPLACE
            )
        else:
            allowed_action_values = {
                PluginArtifactAction.INSTALL.value,
                PluginArtifactAction.REPLACE.value,
                *((PluginArtifactAction.REMOVE.value,) if command == "preview" else ()),
            }
            if requested_action not in allowed_action_values:
                raise ValueError("cursor_plugin_action_invalid")
            action = PluginArtifactAction(requested_action)
        if command == "preview":
            # Only the preview command renders a plan. Computing one here for install/remove
            # would also refuse an already-committed replay before the adapter can reconcile it.
            preview = preview_cursor_plugin(request, target, action, artifact, project_root=project)
            _emit(
                {
                    "action": preview.action.value,
                    "artifact_digest": preview.artifact_digest,
                    "authorization": {
                        "operation": "plugin_artifact_apply",
                        "prepare_command": [
                            "yoetz",
                            "consent",
                            "prepare",
                            "plugin_artifact_apply",
                            "--target-digest",
                            preview.preview_digest,
                        ],
                        "requires_os_authenticated_prompt": True,
                    },
                    "format_profile": preview.format_profile.value,
                    "mcp_ownership": preview.mcp_ownership.value,
                    "mcp_ownership_state": preview.mcp_ownership_state.value,
                    "mcp_route_profile": preview.mcp_route_profile,
                    "preview_digest": preview.preview_digest,
                    "request_id": preview.request_id,
                    "scope": "user",
                    "state_before": preview.state_before.value,
                    "warnings": list(preview.warnings),
                },
                json_output=json_output,
            )
            return 0
        if not accept or preview_digest is None:
            sys.stderr.write("cursor_plugin_exact_preview_acceptance_required\n")
            return 3
        # ``--accept`` only proves the operator typed the exact preview digest. The mutation
        # itself consumes the ADR-016 ``review_only`` single-shot trusted review prepared for
        # this exact digest; the adapter refuses when that authority is absent or unproven.
        review: PluginMutationReviewPort = ElevatedPortableArtifactReview(
            MacOSArtifactUserPresence() if _presence is None else _presence,
            _state=_state,
        )
        authority = _artifact_authority(preview_digest, state=_state)
        result = (
            remove_cursor_plugin(
                request,
                target,
                artifact,
                accepted_preview_digest=preview_digest,
                authority=authority,
                review=review,
                project_root=project,
            )
            if command == "remove"
            else apply_cursor_plugin(
                request,
                target,
                action,
                artifact,
                accepted_preview_digest=preview_digest,
                authority=authority,
                review=review,
                project_root=project,
            )
        )
        _emit(
            {
                "action": result.action.value,
                "artifact_digest": result.artifact_digest,
                "changed_files": list(result.changed_files),
                "format_profile": result.format_profile.value,
                "installed_digest": result.installed_digest,
                "operation_state": result.operation_state.value,
                "preview_digest": result.preview_digest,
                "request_id": result.request_id,
                "scope": "user",
                "state_after": result.state_after.value,
                "state_before": result.state_before.value,
            },
            json_output=json_output,
        )
        return 0
    except (CursorIntegrationError, ValueError) as error:
        reason = error.reason.value if isinstance(error, CursorIntegrationError) else str(error)
        sys.stderr.write(f"{reason}\n")
        return 1

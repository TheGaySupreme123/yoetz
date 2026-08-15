"""The only bridge between the Yoetz terminal UI and the application services.

Everything the interface can do passes through this module, and this module does
nothing on its own authority. It discovers harnesses with the existing discovery
adapter, previews and applies integrations with ``HarnessMcpService`` and
``CodexPluginService``, reads readiness with ``provider_status_report``, and
runs the six canonical operations over the ordinary ``ServiceClient``. Secrets
are the sharpest case: this module never reads one. When a credential or
passphrase is required it hands the *real* terminal back to the existing
confidential ceremony in ``yoetz.cli.unlock``, which opens ``/dev/tty``, checks
that it is the controlling terminal, and disables echo itself. No secret byte
ever reaches a widget, the transcript, a log line, or a snapshot.

Widgets therefore contain no security-relevant logic. If a rule matters — digest
staleness, foreign MCP entries, privacy widening, vault state — it is enforced
by the owning service and merely transcribed here.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from yoetz.tui.models import (
    CheckMode,
    Detection,
    DoctorEntry,
    DoctorReport,
    HarnessOption,
    IntegrationOutcome,
    IntegrationPlan,
    LayerState,
    PrivacyPosture,
    PrivacyRecommendation,
    ProviderOption,
    ProviderPosture,
    ReadinessLayer,
    ReceiptSummary,
    StatusSnapshot,
    VaultPosture,
    WorkDetail,
    WorkItem,
)

if TYPE_CHECKING:  # Runtime imports stay lazy; annotations still type-check.
    from yoetz.ports.harness_mcp import HarnessBinary
    from yoetz.service.confidential_protocol import ProviderCredentialTarget

__all__ = ["RuntimeError_", "YoetzRuntime", "project_detection"]

_MCP_SERVER_NAME: Final = "yoetz"
_HARNESS: Final = "codex"


def _agent_route_detail(provider: ProviderPosture) -> str:
    """Explain the agent-route verdict without restating installation readiness."""

    if provider.agent_route_semantic_ready is None:
        return "the Codex registration could not be read"
    if provider.agent_route_semantic_ready:
        return ""
    if provider.registered_route_profile == "strict":
        return "registered on the strict route; 'yoetz integrate codex mcp preview' to change it"
    return "external review is off for this installation"


def _serve_command_display(route_profile: Literal["policy", "strict"]) -> str:
    """Render the exact argv this route registers, for the screen that asks for approval.

    A fixed string here would show ``yoetz mcp serve`` while registering the strict command,
    which is the one line on that screen the human is being asked to approve.
    """

    # Local import: the ports module stays off the TUI's startup path.
    from yoetz.ports.harness_mcp import MCP_SERVE_COMMAND, MCP_STRICT_SERVE_COMMAND

    return " ".join(MCP_STRICT_SERVE_COMMAND if route_profile == "strict" else MCP_SERVE_COMMAND)


def _mapping(value: object) -> Mapping[str, object]:
    """Narrow an untyped service payload to a string-keyed mapping, or nothing.

    Service results cross this boundary as loosely typed JSON-shaped objects.
    Reading them through one helper means a missing or misshapen block degrades
    to "unknown" everywhere rather than raising at whichever call site read it
    first.
    """

    if isinstance(value, Mapping):
        return {str(key): item for key, item in cast(Mapping[object, object], value).items()}
    return {}


class RuntimeError_(Exception):
    """A bounded operation failure carrying a code the interface may show."""

    def __init__(self, reason: str, message: str, *, details: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = tuple(details)


# ---------------------------------------------------------------------------
# Detection (pure enough to test without a service)
# ---------------------------------------------------------------------------


def _git_root(start: Path) -> Path | None:
    """Find the repository root by walking up for a ``.git`` entry.

    Deliberately does not shell out to git: discovery must stay bounded and must
    not execute a repository-controlled hook or config on a folder the user has
    not yet said they trust.
    """

    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def project_detection(
    cwd: Path,
    *,
    harnesses: Sequence[HarnessOption] = (),
    secure_storage_available: bool = False,
    already_connected: bool = False,
) -> Detection:
    """Describe the project the user is standing in, without changing anything."""

    resolved = cwd.resolve()
    root = _git_root(resolved)
    project_root = root if root is not None else resolved
    return Detection(
        project_root=str(project_root),
        project_name=project_root.name or None,
        is_git_repository=root is not None,
        launched_from_subdirectory=root is not None and root != resolved,
        harnesses=tuple(harnesses),
        secure_storage_available=secure_storage_available,
        already_connected=already_connected,
        cwd=str(resolved),
    )


def _harness_option(binary: object, *, index: int, total: int) -> HarnessOption:
    """Label a discovered installation for humans before paths."""

    path = str(getattr(binary, "executable_path", ""))
    version = getattr(binary, "reported_version", None)
    version_text = version if isinstance(version, str) else None
    lowered = path.lower()
    # A bundled application install and a PATH shim are the same binary contract
    # but very different things to a person choosing between them.
    is_app = any(token in lowered for token in (".app/", "/applications/", "program files"))
    label = f"Codex Desktop {version_text}" if is_app else f"Codex CLI {version_text or ''}".strip()
    if version_text is None and is_app:
        label = "Codex Desktop"
    description = "Application installation" if is_app else "Command-line installation"
    recommended = index == 0 and total >= 1
    if recommended:
        description = f"{description} · recommended"
    return HarnessOption(
        executable_path=path,
        reported_version=version_text,
        label=label,
        description=description,
        recommended=recommended,
    )


def _secure_storage_available() -> bool:
    """Probe whether an OS credential store is usable, never storing anything."""

    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except Exception:
        return False
    try:
        return not isinstance(keyring.get_keyring(), FailKeyring)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _WorkSession:
    """Identifiers returned by ``start``; reused for the task-scoped operations."""

    task_id: str
    session_id: str
    writer_id: str
    frontier: object


class YoetzRuntime:
    """Async facade over the existing Yoetz services for one terminal session."""

    def __init__(self, *, cwd: Path | None = None) -> None:
        self._cwd = (cwd or Path.cwd()).resolve()
        self._sessions: dict[str, _WorkSession] = {}
        self._opened_titles: list[str] = []

    # -- discovery ------------------------------------------------------

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def opened_titles(self) -> tuple[str, ...]:
        """Task titles opened in this session, newest first.

        This is a convenience for re-opening, not a task index: the control
        protocol has no task-listing operation and the interface must not invent
        one. Nothing here is persisted or treated as authoritative.
        """

        return tuple(reversed(self._opened_titles))

    def discover_harnesses(self) -> tuple[HarnessOption, ...]:
        from yoetz.adapters.integrations.codex_discovery import discover_codex_binaries

        binaries = discover_codex_binaries()
        return tuple(
            _harness_option(binary, index=index, total=len(binaries))
            for index, binary in enumerate(binaries)
        )

    async def detect(self) -> Detection:
        """Everything the welcome screen shows, gathered without mutation."""

        harnesses = self.discover_harnesses()
        connected = False
        for harness in harnesses:
            try:
                if await self.mcp_state(harness) == "yoetz_owned":
                    connected = True
                    break
            except RuntimeError_:
                continue
        return project_detection(
            self._cwd,
            harnesses=harnesses,
            secure_storage_available=_secure_storage_available(),
            already_connected=connected,
        )

    def _binary_for(self, option: HarnessOption) -> HarnessBinary:
        from yoetz.ports.harness_mcp import HarnessBinary
        from yoetz.ports.integrations import HarnessId

        return HarnessBinary(
            harness_id=HarnessId.CODEX,
            executable_path=option.executable_path,
            reported_version=option.reported_version,
            compatibility="untested",
        )

    async def mcp_state(self, option: HarnessOption) -> str:
        from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter
        from yoetz.application.harness_mcp import HarnessMcpService
        from yoetz.ports.harness_mcp import McpRegistrationError

        service = HarnessMcpService(CodexMcpAdapter())
        try:
            state = await service.status(self._binary_for(option))
        except McpRegistrationError as error:
            raise RuntimeError_(error.reason.value, "the Codex registration could not be read")
        return str(state.value)

    async def run_privacy_setup(
        self, recipe_hint: str | None, *, offer_recommended: bool = False
    ) -> object:
        """Hand the trusted questionnaire to the CLI implementation."""

        from yoetz.cli.privacy_setup import run_privacy_setup
        from yoetz.cli.unlock import HumanCeremonyCliError
        from yoetz.ports.control import ControlError

        if recipe_hint is not None and recipe_hint not in {
            "private",
            "metadata_only",
            "assisted_review",
            "expanded_review",
            "custom",
        }:
            raise RuntimeError_("privacy_recipe_invalid", "the privacy recipe is invalid")
        try:
            return await run_privacy_setup(
                recipe_hint=cast(
                    Literal[
                        "private",
                        "metadata_only",
                        "assisted_review",
                        "expanded_review",
                        "custom",
                    ]
                    | None,
                    recipe_hint,
                ),
                offer_recommended=offer_recommended,
                workspace_locator=self._cwd,
            )
        except (ControlError, HumanCeremonyCliError, OSError, ValueError) as error:
            raise RuntimeError_(
                getattr(error, "reason", "privacy_setup_failed"),
                "the trusted privacy ceremony could not be completed",
            ) from error

    def privacy_recommendation(
        self, posture: PrivacyPosture | None = None
    ) -> PrivacyRecommendation:
        """The same recommendation rule the CLI uses, never a second opinion."""

        from yoetz.cli.privacy_setup import recommended_privacy_recipe

        try:
            recipe = recommended_privacy_recipe()
        except Exception:
            # Reading the configured provider binding can fail on its own (an unrecognized
            # `YOETZ_*` variable, an unreadable file). Recommend the closed posture rather
            # than propagating: never recommend enabling egress on the strength of a
            # configuration that could not be read.
            recipe = "private"
        if recipe == "assisted_review":
            grant_sentence = (
                " This repository already has the required grant."
                if posture is not None and posture.repository_grant_state == "granted"
                else " Approval creates the required grant only for this repository."
            )
            return PrivacyRecommendation(
                recipe,
                "This exact provider route has current reviewed no-training and retention evidence, "
                "so bounded Assisted review is available for this repository." + grant_sentence,
                "It may disclose selected problem-local ordinary user content; the trusted policy "
                "review shows the exact boundary and provider caveats before approval.",
            )
        if recipe == "metadata_only":
            return PrivacyRecommendation(
                recipe,
                "It enables semantic review while disclosing the least that still works, and "
                "asks before every provider request.",
                "In exchange, the reviewer sees structural metadata only, so it cannot judge "
                "whether a claim is actually supported.",
            )
        return PrivacyRecommendation(
            recipe,
            "No current eligible exact provider route is configured, so this keeps network "
            "egress off entirely.",
            "In exchange, there is no external semantic review at all.",
        )

    # -- integration ----------------------------------------------------

    async def integration_plan(
        self,
        option: HarnessOption,
        codex_home: Path,
        route_profile: Literal["policy", "strict"] | None = None,
    ) -> IntegrationPlan:
        """Build the exact proposed change, previewing through the owning services.

        ``route_profile`` is the answer the human just gave to "how should Yoetz review work?".
        Omit it only where nothing was asked -- post-setup ``/connect`` -- and the configured
        posture is then the authority.
        """

        from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter
        from yoetz.application.codex_plugin import CodexPluginService
        from yoetz.application.harness_mcp import HarnessMcpService
        from yoetz.cli.setup import (
            check_policy_preview,
            codex_activation_preview,
            configured_mcp_route_profile,
            project_skill_preview,
        )
        from yoetz.ports.harness_mcp import McpRegistrationError, McpRegistrationState
        from yoetz.ports.integrations import IntegrationError, IntegrationScope, IntegrationTarget

        binary = self._binary_for(option)
        root = self.project_root()
        route = configured_mcp_route_profile() if route_profile is None else route_profile
        try:
            mcp_preview = await HarnessMcpService(CodexMcpAdapter(route_profile=route)).preview(
                binary
            )
        except McpRegistrationError as error:
            raise RuntimeError_(error.reason.value, "the Codex registration could not be previewed")
        target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(root))
        skill_preview = await project_skill_preview(root)
        try:
            activation_preview = codex_activation_preview(binary, codex_home, root)
        except IntegrationError as error:
            raise RuntimeError_(error.reason.value, "the Codex activation could not be previewed")
        plugin_preview = CodexPluginService().preview(
            target,
            codex_version=activation_preview.codex_version,
        )
        policy = check_policy_preview(root)
        digest = policy.get("policy_digest")
        checks = policy.get("check_ids")
        return IntegrationPlan(
            harness_label=option.label,
            executable_path=option.executable_path,
            codex_home=str(activation_preview.codex_home),
            reported_version=option.reported_version,
            project_root=str(root),
            route_profile=route,
            mcp_command=_serve_command_display(route),
            mcp_server_name=_MCP_SERVER_NAME,
            policy_digest=digest if isinstance(digest, str) else None,
            planned_check_ids=tuple(str(item) for item in checks)
            if isinstance(checks, (tuple, list))
            else (),
            planned_file_count=(
                int(getattr(plugin_preview, "planned_file_count", 0))
                + len(skill_preview.file_changes)
            ),
            managed_paths=(
                str(root / ".agents" / "skills" / "yoetz"),
                str(root / ".agents" / "plugins" / "yoetz"),
            ),
            state_before=str(mcp_preview.state_before.value),
            already_registered=mcp_preview.state_before is McpRegistrationState.YOETZ_OWNED,
            foreign_entry=mcp_preview.state_before is McpRegistrationState.FOREIGN_PRESENT,
            preview_digest=str(mcp_preview.preview_digest),
            skill_preview_digest=skill_preview.preview_digest,
            activation_preview_digest=activation_preview.preview_digest,
            activation_marketplace_path=str(root / ".agents" / "plugins" / "marketplace.json"),
            activation_config_path=str(activation_preview.codex_home / "config.toml"),
            activation_marketplace_text=activation_preview.marketplace_bytes.decode("utf-8"),
            activation_config_block=activation_preview.config_toml_block,
            activation_plugin_source_digest=activation_preview.plugin_source_digest,
            activation_inventory_verified=activation_preview.inspection.inventory_verified,
            activation_plugin_install_path=str(activation_preview.plugin_install_path),
            activation_plugin_install_digest=activation_preview.plugin_install_digest,
            activation_executable_digest=activation_preview.executable_digest,
            activation_codex_version=activation_preview.codex_version,
            activation_probe_command=activation_preview.probe_command,
            activation_inventory_command=activation_preview.inventory_command,
            activation_install_command=activation_preview.install_command,
            activation_probe_environment=activation_preview.probe_environment,
            activation_environment=activation_preview.activation_environment,
            activation_marketplace_preimage_digest=(activation_preview.marketplace_preimage_digest),
            activation_config_preimage_digest=activation_preview.config_preimage_digest,
            activation_cache_mutation_planned=activation_preview.cache_mutation_planned,
        )

    async def apply_integration(
        self, option: HarnessOption, plan: IntegrationPlan
    ) -> IntegrationOutcome:
        """Apply exactly the plan the human approved, echoing back both digests.

        The digests are not decoration. ``apply_codex_integration`` re-previews
        and refuses when either has moved since the approval screen was drawn,
        which is the same staleness gate ``integrate mcp install
        --preview-digest`` already enforces.

        The route travels on the plan for the same reason: it is inside the preview digest, so
        resolving it a second time here could only ever disagree with what was approved.
        """

        from yoetz.cli.setup import apply_codex_integration

        report = await apply_codex_integration(
            self._binary_for(option),
            route_profile=plan.route_profile,
            workspace=self.project_root(),
            approved_preview_digest=plan.preview_digest,
            approved_skill_preview_digest=plan.skill_preview_digest,
            approved_activation_digest=plan.activation_preview_digest,
            approved_policy_digest=plan.policy_digest,
            codex_home=Path(plan.codex_home),
        )
        return self._integration_outcome(report)

    def _integration_outcome(self, report: Mapping[str, object]) -> IntegrationOutcome:
        outcome = str(report.get("outcome") or "unknown")
        reason = report.get("reason")
        plugin_map = _mapping(report.get("plugin"))
        consent_map = _mapping(report.get("observation_consent"))
        policy_map = _mapping(report.get("check_policy"))

        registered = outcome in {"registered", "already_registered"}
        verified = registered and report.get("state") == "yoetz_owned"
        plugin_installed = plugin_map.get("presence") == "installed"
        consent_active = consent_map.get("outcome") == "granted"
        policy_trusted = policy_map.get("outcome") == "trusted"
        policy_absent = policy_map.get("outcome") == "absent" or not policy_map

        layers = (
            ReadinessLayer(
                "mcp_registered",
                "MCP registered",
                LayerState.VERIFIED if registered else LayerState.BLOCKED,
            ),
            ReadinessLayer(
                "mcp_verified",
                "MCP verified",
                LayerState.VERIFIED if verified else LayerState.UNPROVEN,
            ),
            ReadinessLayer(
                "plugin_installed",
                "Guidance installed",
                LayerState.VERIFIED if plugin_installed else LayerState.BLOCKED,
            ),
            ReadinessLayer(
                "hooks_installed",
                "Structural hooks installed",
                LayerState.VERIFIED
                if plugin_map.get("trust_observable") is True
                else LayerState.UNPROVEN,
            ),
            ReadinessLayer(
                "project_consent",
                "Project consent active",
                LayerState.VERIFIED if consent_active else LayerState.NOT_CONFIGURED,
            ),
            ReadinessLayer(
                "policy_digest_trusted",
                "Approved-check policy trusted",
                LayerState.NOT_CONFIGURED
                if policy_absent
                else (LayerState.VERIFIED if policy_trusted else LayerState.UNPROVEN),
                detail=""
                if policy_trusted or policy_absent
                else str(policy_map.get("outcome") or ""),
            ),
        )
        return IntegrationOutcome(
            outcome=outcome,
            reason=str(reason) if isinstance(reason, str) else None,
            layers=layers,
            foreign_entry=reason == "foreign_entry_present",
            existing_entry_detail=str(report.get("state") or ""),
        )

    async def foreign_entry_detail(self, option: HarnessOption) -> tuple[str, ...]:
        """Show what already owns the name, without offering to remove it."""

        binary = self._binary_for(option)
        executable = str(getattr(binary, "executable_path", ""))
        return (
            f'An MCP entry named "{_MCP_SERVER_NAME}" already exists for this Codex '
            "installation and was not created by Yoetz.",
            f"Codex executable: {executable}",
            "Inspect it with: codex mcp get yoetz",
            "Yoetz will never replace or remove an entry it does not own.",
        )

    def project_root(self) -> Path:
        root = _git_root(self._cwd)
        return root if root is not None else self._cwd

    # -- service --------------------------------------------------------

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[Any]:
        from yoetz.cli.app import build_service_client
        from yoetz.ports.control import ControlClientKind, ControlError, WorkspaceLocator

        try:
            client = await build_service_client(
                ControlClientKind.UI,
                workspace_locator=WorkspaceLocator(str(self._cwd.resolve(strict=True))),
            )
        except ControlError as error:
            raise RuntimeError_(error.reason, "the local Yoetz service is not reachable")
        try:
            yield client
        finally:
            await client.close()

    async def vault_posture(self) -> VaultPosture:
        from yoetz.ports.control import ControlError

        try:
            async with self._client() as client:
                status = await client.service_status()
        except RuntimeError_, ControlError:
            return VaultPosture(reachable=False, state=None, vault_mode=None)
        state = getattr(getattr(status, "state", None), "value", None)
        return VaultPosture(
            reachable=True,
            state=state if isinstance(state, str) else None,
            vault_mode=getattr(status, "vault_mode", None),
        )

    async def service_lock(self) -> str:
        async with self._client() as client:
            status = await client.lock()
        return str(getattr(getattr(status, "state", None), "value", "unknown"))

    async def service_stop(self) -> str:
        async with self._client() as client:
            result = await client.stop()
        return str(getattr(result, "state", "draining"))

    # -- privacy --------------------------------------------------------

    async def privacy_posture(self) -> PrivacyPosture:
        from yoetz.domain.values import JsonObject
        from yoetz.ports.control import ControlError

        try:
            async with self._client() as client:
                effective = await client.privacy_get_setup(JsonObject({"schema_version": "2.0.0"}))
        except RuntimeError_, ControlError:
            return PrivacyPosture(profile=None, llm_inference_enabled=None, readable=False)
        response = _mapping(effective)
        policy_map = _mapping(response.get("composed_policy"))
        if not policy_map:
            return PrivacyPosture(profile=None, llm_inference_enabled=None, readable=False)
        profile = policy_map.get("profile")
        enabled: list[str] = []
        llm_enabled: bool | None = None
        channels = policy_map.get("channel_policies")
        if isinstance(channels, (list, tuple)):
            for entry in cast("Sequence[object]", channels):
                row = _mapping(entry)
                if not row:
                    continue
                name = row.get("channel")
                if row.get("enabled") is True and isinstance(name, str):
                    enabled.append(name)
                if name == "llm_inference":
                    llm_enabled = row.get("enabled") is True
        never = policy_map.get("forbidden_data_kinds")
        never_send = (
            tuple(str(item) for item in cast("Sequence[object]", never))
            if isinstance(never, (list, tuple))
            else ()
        )
        network_raw = policy_map.get("network_egress_permitted")
        network_egress = network_raw if type(network_raw) is bool else None
        return PrivacyPosture(
            profile=profile if isinstance(profile, str) else None,
            llm_inference_enabled=llm_enabled,
            readable=True,
            never_send=never_send,
            enabled_channels=tuple(enabled),
            network_egress_permitted=network_egress,
            repository_grant_state=cast(
                Literal["granted", "missing"] | None,
                response.get("grant_state")
                if response.get("grant_state") in {"granted", "missing"}
                else None,
            ),
            repository_migration_state=cast(
                str | None,
                response.get("migration_state")
                if response.get("migration_state")
                in {
                    "not_applicable",
                    "legacy_route_available",
                    "first_repository_available",
                    "consumed",
                }
                else None,
            ),
        )

    # -- provider -------------------------------------------------------

    def provider_options(self) -> tuple[ProviderOption, ...]:
        """The reviewed presets plus the owner-declared HTTPS escape hatch."""

        from yoetz.config.write import PROVIDER_PRESETS

        labels = {
            "official_openai": "OpenAI",
            "fireworks": "Fireworks AI",
            "anthropic": "Anthropic",
            "google_gemini": "Google Gemini",
            "openrouter": "OpenRouter",
            "vercel_ai_gateway": "Vercel AI Gateway",
        }
        options = [
            ProviderOption(
                choice=preset.choice,
                label=labels.get(preset.choice, preset.provider_id),
                provider_id=preset.provider_id,
                host=preset.host,
                base_path_prefix=preset.base_path_prefix,
                default_model=preset.default_model,
                api_style=preset.api_style,
                endpoint_profile_id=preset.endpoint_profile_id,
                endpoint_profile_version=preset.endpoint_profile_version,
            )
            for key, preset in PROVIDER_PRESETS.items()
            if key in labels
        ]
        options.append(
            ProviderOption(
                choice="owner_declared",
                label="Custom OpenAI-compatible endpoint",
                provider_id="owner-declared",
                host="",
                base_path_prefix="",
                default_model="",
                api_style="responses",
                endpoint_profile_id="owner-declared-openai-responses",
                endpoint_profile_version="1.0.0",
                requires_origin=True,
            )
        )
        return tuple(options)

    def save_provider_binding(
        self, option: ProviderOption, model: str, *, https_origin: str | None = None
    ) -> None:
        """Write only the nonsecret endpoint binding. Never a credential."""

        from yoetz.cli.provider_binding import apply_provider_endpoint_choice
        from yoetz.config.models import ConfigError

        try:
            apply_provider_endpoint_choice(
                cast(Any, option.choice), model=model, https_origin=https_origin
            )
        except (ConfigError, OSError, ValueError) as error:
            reason = getattr(error, "reason_code", "provider_binding_invalid")
            raise RuntimeError_(str(reason), "that provider binding is not valid")

    async def provider_posture(self) -> ProviderPosture:
        from yoetz.cli.provider_status import provider_status_report

        report = await provider_status_report(workspace_locator=self._cwd)
        endpoint_map = _mapping(report.get("endpoint"))
        blockers: list[tuple[str, str]] = []
        raw_blockers = report.get("blockers")
        if isinstance(raw_blockers, (list, tuple)):
            for item in cast("Sequence[object]", raw_blockers):
                row = _mapping(item)
                if row:
                    blockers.append((str(row.get("condition")), str(row.get("state") or "unknown")))
        route_map = _mapping(report.get("mcp_route"))
        raw_agent_ready = report.get("agent_route_semantic_ready")
        return ProviderPosture(
            agent_route_semantic_ready=(
                raw_agent_ready if isinstance(raw_agent_ready, bool) else None
            ),
            registered_route_profile=cast(str | None, route_map.get("registered_profile")),
            endpoint_bound=report.get("endpoint_bound") is True,
            provider_id=cast(str | None, endpoint_map.get("provider_id")),
            model=cast(str | None, endpoint_map.get("model")),
            endpoint_profile_id=cast(str | None, endpoint_map.get("endpoint_profile_id")),
            credential_connected=cast(bool | None, report.get("credential_connected")),
            llm_inference_enabled=cast(bool | None, report.get("llm_inference_enabled")),
            semantic_enabled=report.get("semantic_enabled") is True,
            semantic_ready=report.get("semantic_ready") is True,
            readiness_determinable=report.get("readiness_determinable") is True,
            blockers=tuple(blockers),
        )

    # -- confidential ceremonies ---------------------------------------

    def credential_target(
        self, repository_privacy_commitment: str | None = None
    ) -> ProviderCredentialTarget:
        """Build the nonsecret credential identifiers for the ceremony."""

        from yoetz.config.load import load_config
        from yoetz.service.confidential_protocol import ProviderCredentialTarget
        from yoetz.service.vault import provider_credential_profile_binding

        provider = load_config({}, {}, None).provider
        if provider is None:
            raise RuntimeError_("provider_not_configured", "choose a provider and model first")
        binding = provider_credential_profile_binding(
            provider.provider_id,
            provider.model,
            provider.endpoint_profile_id,
            provider.endpoint_profile_version,
        )
        return ProviderCredentialTarget(
            action="set",
            provider_id=binding.provider_id,
            model_id=binding.model_id,
            endpoint_profile_id=binding.endpoint_profile_id,
            endpoint_profile_version=binding.endpoint_profile_version,
            purpose=binding.purpose,
            scope_digest=binding.authorization_scope_digest,
            purpose_digest=binding.purpose_digest,
            repository_privacy_commitment=repository_privacy_commitment,
        )

    async def store_provider_credential(self) -> str:
        """Run the existing confidential ceremony. No secret crosses this call."""

        from yoetz.cli.privacy_setup import get_privacy_setup_snapshot
        from yoetz.cli.unlock import (
            HumanCeremonyCliError,
            load_auto_unlock_reauthentication,
            set_provider_credential,
        )

        snapshot = await get_privacy_setup_snapshot(self._cwd)
        repository_commitment = snapshot.bound_scope.get("workspace_ref_commitment")
        if type(repository_commitment) is not str:
            raise RuntimeError_(
                "repository_privacy_scope_unavailable",
                "the current repository is not bound to privacy authority",
            )
        target = self.credential_target(repository_commitment)
        try:
            # A Keychain-provisioned vault has a passphrase the human never saw; supply it here
            # so the ceremony asks only for the provider key.
            result = await set_provider_credential(
                target, None, load_auto_unlock_reauthentication()
            )
        except HumanCeremonyCliError as error:
            raise RuntimeError_(error.reason, "the credential ceremony did not complete")
        return str(getattr(result, "activation_status", "unknown"))

    async def initialize_passphrase_vault(self) -> None:
        from yoetz.cli.unlock import HumanCeremonyCliError, initialize_passphrase_vault

        try:
            await initialize_passphrase_vault()
        except HumanCeremonyCliError as error:
            raise RuntimeError_(error.reason, "the vault could not be initialized")

    async def initialize_system_keyring(self) -> None:
        from yoetz.cli.unlock import HumanCeremonyCliError, retry_keyring

        try:
            await retry_keyring(expected_mode="uninitialized")
        except HumanCeremonyCliError as error:
            raise RuntimeError_(error.reason, "system secure storage could not be initialized")

    async def unlock_vault(self) -> None:
        from yoetz.cli.unlock import HumanCeremonyCliError, retry_keyring, unlock_vault

        posture = await self.vault_posture()
        try:
            if posture.vault_mode == "os_keyring":
                await retry_keyring()
            elif posture.vault_mode == "passphrase":
                await unlock_vault()
            else:
                raise RuntimeError_(
                    "vault_uninitialized", "the vault has not been set up on this machine yet"
                )
        except HumanCeremonyCliError as error:
            raise RuntimeError_(error.reason, "the vault could not be unlocked")

    # -- readiness ------------------------------------------------------

    async def status_snapshot(self) -> StatusSnapshot:
        """Compose every readiness layer, keeping each one separately falsifiable."""

        from yoetz.adapters.integrations.codex_plugin import inspect_plugin
        from yoetz.ports.integrations import IntegrationError, IntegrationScope, IntegrationTarget

        root = self.project_root()
        harnesses = self.discover_harnesses()
        layers: list[ReadinessLayer] = []

        detected = bool(harnesses)
        layers.append(
            ReadinessLayer(
                "harness_detected",
                "Harness detected",
                LayerState.VERIFIED if detected else LayerState.NOT_CONFIGURED,
                detail=harnesses[0].label if detected else "no Codex installation found",
            )
        )

        mcp_state: str | None = None
        if detected:
            try:
                mcp_state = await self.mcp_state(harnesses[0])
            except RuntimeError_ as error:
                mcp_state = None
                layers.append(
                    ReadinessLayer(
                        "mcp_registered", "MCP registered", LayerState.UNKNOWN, error.reason
                    )
                )
        if mcp_state is not None:
            registered = mcp_state in {"yoetz_owned", "foreign_present"}
            layers.append(
                ReadinessLayer(
                    "mcp_registered",
                    "MCP registered",
                    LayerState.VERIFIED if registered else LayerState.NOT_CONFIGURED,
                    detail=mcp_state,
                )
            )
            layers.append(
                ReadinessLayer(
                    "mcp_verified",
                    "MCP verified",
                    LayerState.VERIFIED
                    if mcp_state == "yoetz_owned"
                    else (
                        LayerState.BLOCKED
                        if mcp_state == "foreign_present"
                        else LayerState.NOT_CONFIGURED
                    ),
                    detail="a connection Yoetz does not own holds this name"
                    if mcp_state == "foreign_present"
                    else "",
                )
            )
        elif not detected:
            layers.append(
                ReadinessLayer("mcp_registered", "MCP registered", LayerState.NOT_CONFIGURED)
            )
            layers.append(ReadinessLayer("mcp_verified", "MCP verified", LayerState.NOT_CONFIGURED))
        else:
            layers.append(ReadinessLayer("mcp_verified", "MCP verified", LayerState.UNKNOWN))

        try:
            inspection = inspect_plugin(
                IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(root)),
                codex_version=harnesses[0].reported_version if detected else None,
            )
            presence = str(inspection.presence.value)
            trust_observable = bool(inspection.trust_observable)
        except IntegrationError as error:
            presence = "unknown"
            trust_observable = False
            layers.append(
                ReadinessLayer(
                    "plugin_installed",
                    "Guidance installed",
                    LayerState.UNKNOWN,
                    error.reason.value,
                )
            )
        else:
            layers.append(
                ReadinessLayer(
                    "plugin_installed",
                    "Guidance installed",
                    LayerState.VERIFIED if presence == "installed" else LayerState.NOT_CONFIGURED,
                )
            )
        layers.append(
            ReadinessLayer(
                "hooks_installed",
                "Structural hooks installed",
                LayerState.VERIFIED
                if presence == "installed" and trust_observable
                else (
                    LayerState.UNPROVEN if presence == "installed" else LayerState.NOT_CONFIGURED
                ),
            )
        )

        consent_active = self._consent_active(root)
        layers.append(
            ReadinessLayer(
                "project_consent",
                "Project consent active",
                LayerState.VERIFIED if consent_active else LayerState.NOT_CONFIGURED,
            )
        )
        layers.append(self._policy_digest_layer(root))

        vault = await self.vault_posture()
        layers.append(
            ReadinessLayer(
                "service_reachable",
                "Local service reachable",
                LayerState.VERIFIED if vault.reachable else LayerState.BLOCKED,
                detail=vault.state or "not running",
            )
        )
        layers.append(
            ReadinessLayer(
                "vault_ready",
                "Vault ready",
                LayerState.VERIFIED
                if vault.ready
                else (LayerState.UNPROVEN if vault.reachable else LayerState.UNKNOWN),
                detail=vault.vault_mode or "",
            )
        )
        layers.append(
            ReadinessLayer(
                "local_checks",
                "Local deterministic checks",
                LayerState.VERIFIED if vault.ready else LayerState.UNPROVEN,
                detail="" if vault.ready else "the local service must be ready first",
            )
        )

        provider = await self.provider_posture()
        privacy = await self.privacy_posture()
        layers.extend(self._provider_layers(provider, privacy))

        # The control protocol has no task-listing operation. These values mean
        # work is unreadable here; they are not a claim that the task list is empty.
        open_work, open_findings, readable = 0, 0, False
        return StatusSnapshot(
            project_root=str(root),
            layers=tuple(layers),
            privacy=privacy,
            open_work=open_work,
            open_findings=open_findings,
            work_readable=readable,
        )

    def _consent_active(self, root: Path) -> bool:
        try:
            from yoetz.adapters.integrations.observation_local import LocalObservationStore

            store = LocalObservationStore()
            consent = store.consent_for(store.workspace_commitment(str(root)))
        except Exception:
            return False
        return consent is not None and bool(consent.active)

    def _policy_digest_layer(self, root: Path) -> ReadinessLayer:
        from yoetz.cli.setup import check_policy_preview

        preview = check_policy_preview(root)
        digest = preview.get("policy_digest")
        if not isinstance(digest, str):
            return ReadinessLayer(
                "policy_digest_trusted",
                "Approved-check policy trusted",
                LayerState.NOT_CONFIGURED,
                detail="this project declares no approved checks",
            )
        try:
            from yoetz.adapters.integrations.observation_local import LocalObservationStore

            store = LocalObservationStore()
            commitment = store.workspace_commitment(str(root))
            trusted = store.policy_digest_is_trusted(commitment, digest)
        except Exception:
            return ReadinessLayer(
                "policy_digest_trusted", "Approved-check policy trusted", LayerState.UNKNOWN
            )
        return ReadinessLayer(
            "policy_digest_trusted",
            "Approved-check policy trusted",
            LayerState.VERIFIED if trusted else LayerState.UNPROVEN,
            detail="" if trusted else "the project policy bytes changed since approval",
        )

    def _provider_layers(
        self, provider: ProviderPosture, privacy: PrivacyPosture
    ) -> tuple[ReadinessLayer, ...]:
        return (
            ReadinessLayer(
                "provider_binding",
                "Provider binding saved",
                LayerState.VERIFIED if provider.endpoint_bound else LayerState.NOT_CONFIGURED,
                detail=f"{provider.provider_id or ''} {provider.model or ''}".strip(),
            ),
            ReadinessLayer(
                "credential_stored",
                "Credential stored",
                LayerState.VERIFIED
                if provider.credential_connected is True
                else (
                    LayerState.UNKNOWN
                    if provider.credential_connected is None
                    else LayerState.NOT_CONFIGURED
                ),
            ),
            # Deliberately never inferred from a stored credential.
            ReadinessLayer(
                "provider_transport_tested",
                "Provider connection tested",
                LayerState.VERIFIED if provider.transport_tested else LayerState.UNPROVEN,
                detail="" if provider.transport_tested else "no live probe has run",
            ),
            ReadinessLayer(
                "semantic_evaluator",
                "Deeper-review evaluator composed",
                LayerState.VERIFIED if provider.semantic_ready else LayerState.NOT_CONFIGURED,
            ),
            ReadinessLayer(
                "privacy_permission",
                "Privacy permits external review",
                LayerState.VERIFIED
                if privacy.llm_inference_enabled is True
                else (
                    LayerState.UNKNOWN
                    if privacy.llm_inference_enabled is None
                    else LayerState.NOT_CONFIGURED
                ),
                detail=privacy.summary,
            ),
            ReadinessLayer(
                "semantic_review_ready",
                "Deeper review ready",
                LayerState.VERIFIED if provider.semantic_ready else LayerState.NOT_CONFIGURED,
                detail="" if provider.semantic_ready else "external review is off",
            ),
            # Deliberately its own layer rather than folded into the one above: the installation
            # can be ready while the registered agent route cannot dispatch, and reporting one
            # verdict for both would make a strict registration read as a broken installation.
            ReadinessLayer(
                "agent_route_review_ready",
                "Codex agent route permits deeper review",
                LayerState.UNKNOWN
                if provider.agent_route_semantic_ready is None
                else (
                    LayerState.VERIFIED
                    if provider.agent_route_semantic_ready
                    else LayerState.NOT_CONFIGURED
                ),
                detail=_agent_route_detail(provider),
            ),
        )

    # -- the six canonical operations ----------------------------------

    async def open_task(self, title: str) -> WorkDetail:
        """Attach to one task by title through ``start``, then read its views.

        There is no task-index operation in the control protocol and this
        interface does not add one; a task is reached by the title the agent
        used for it.
        """

        from yoetz.protocol.models import StartRequestModel

        request = StartRequestModel.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": self._request_id(),
                "mode": "attach",
                "task_title": title,
                "requested_view": "compact",
            }
        )
        async with self._client() as client:
            result = await client.start(request)
            success = self._unwrap(result, "that task could not be opened")
            session = _WorkSession(
                task_id=str(success.task_id),
                session_id=str(success.session_id),
                writer_id=str(success.writer_id),
                frontier=success.frontier,
            )
            self._sessions[title] = session
            if title not in self._opened_titles:
                self._opened_titles.append(title)
            compact = success.compact
        return self._work_detail(title, session, compact)

    def _work_detail(self, title: str, session: _WorkSession, compact: object) -> WorkDetail:
        coverage = getattr(compact, "coverage", None)
        gaps = tuple(str(item) for item in getattr(coverage, "known_gaps", ()) or ())
        open_obligation_count = getattr(compact, "open_obligation_count", None)
        item = WorkItem(
            subject_id=session.task_id,
            title=title,
            state=str(getattr(compact, "ledger_freshness", "unknown")),
            open_findings=int(getattr(compact, "unresolved_finding_count", 0)),
            last_check="not run in this session",
            updated=str(getattr(session.frontier, "sequence", "")),
        )
        return WorkDetail(
            item=item,
            evidence_count=(None if open_obligation_count is None else int(open_obligation_count)),
            coverage=gaps or ("no gaps recorded",),
            limitations=tuple(str(code) for code in getattr(compact, "gaps", ()) or ()),
            receipt_available=False,
        )

    async def run_check(self, title: str, mode: CheckMode) -> tuple[str, tuple[str, ...]]:
        """Run one check in the requested mode and report it without softening."""

        from yoetz.protocol.models import CheckRequestModel

        session = self._sessions.get(title)
        if session is None:
            raise RuntimeError_("task_not_open", "open the task first")
        request = CheckRequestModel.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": self._request_id(),
                "session_id": session.session_id,
                "writer_id": session.writer_id,
                "expected_frontier": self._frontier(session),
                "mode": mode.value,
            }
        )
        async with self._client() as client:
            result = await client.check(request)
        success = self._unwrap(result, "the check could not be completed")
        from yoetz.cli.render import render_human_check

        return str(success.verdict), tuple(render_human_check(success).splitlines())

    async def build_receipt(self, title: str, output_format: str) -> ReceiptSummary:
        from yoetz.protocol.models import ReceiptRequestModel

        session = self._sessions.get(title)
        if session is None:
            raise RuntimeError_("task_not_open", "open the task first")
        request = ReceiptRequestModel.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": self._request_id(),
                "task_id": session.task_id,
                "session_id": session.session_id,
                "writer_id": session.writer_id,
                "expected_frontier": self._frontier(session),
                "format": output_format,
                "include": "summary",
                "redaction_profile": "standard",
            }
        )
        async with self._client() as client:
            result = await client.receipt(request)
        success = self._unwrap(result, "the receipt could not be built")
        coverage = getattr(success, "coverage", None)
        gaps = tuple(str(item) for item in getattr(coverage, "known_gaps", ()) or ())
        return ReceiptSummary(
            subject_id=session.task_id,
            verdict=str(success.conclusion),
            coverage=gaps or ("no gaps recorded",),
            open_findings=int(getattr(success, "suppressed_finding_count", 0)),
            limitations=gaps,
            semantic_available=False,
            freshness=str(getattr(coverage, "ledger_freshness", "unknown")),
            verified=("deterministic checks recorded in this receipt",),
            not_verified=("external semantic review did not contribute to this receipt",),
        )

    def _frontier(self, session: _WorkSession) -> object:
        frontier = session.frontier
        return {
            "sequence": getattr(frontier, "sequence", 0),
            "digest": getattr(frontier, "digest", None),
        }

    def _request_id(self) -> str:
        from yoetz.protocol.ids import IdKind, new_id

        return new_id(IdKind.REQUEST)

    def _unwrap(self, result: object, message: str) -> Any:
        payload = getattr(result, "result", result)
        if getattr(payload, "ok", False) is not True:
            error = getattr(payload, "error", None)
            reason = str(getattr(error, "code", "operation_failed"))
            detail = str(getattr(error, "message", message))
            raise RuntimeError_(reason, detail)
        return payload

    # -- package update advisory ----------------------------------------

    async def package_update_advisory(self, *, allow_network: bool = True):
        """Best-effort structural package update advisory for interactive surfaces only.

        Fail closed when policy is unreadable or denies ``update_checks``. Network errors
        yield no tip (or a soft doctor note when the caller is ``doctor``).
        """

        from yoetz.application.package_update import resolve_package_update_advisory

        posture = await self.privacy_posture()
        return await resolve_package_update_advisory(
            network_egress_permitted=posture.network_egress_permitted,
            update_checks_enabled="update_checks" in posture.enabled_channels,
            allow_network=allow_network and posture.update_checks_permitted,
        )

    # -- doctor ---------------------------------------------------------

    async def doctor(self) -> DoctorReport:
        """Bounded read-only diagnosis. Never mutates installation state."""

        from yoetz import __version__

        entries: list[DoctorEntry] = []
        supported = (3, 14) <= sys.version_info[:2] < (3, 15)
        entries.append(
            DoctorEntry(
                "runtime",
                "Python runtime",
                LayerState.VERIFIED if supported else LayerState.BLOCKED,
                detail=".".join(str(part) for part in sys.version_info[:3]),
                remediation=""
                if supported
                else "Yoetz requires Python 3.14; reinstall with 'uv tool install yoetz'",
            )
        )
        package_detail = __version__
        package_state = LayerState.VERIFIED
        package_remediation = ""
        try:
            advisory = await self.package_update_advisory(allow_network=True)
        except Exception:
            advisory = None
        if advisory is not None and advisory.is_newer and advisory.latest_version is not None:
            package_state = LayerState.UNPROVEN
            package_detail = f"{advisory.installed_version} (available {advisory.latest_version})"
            package_remediation = f"{advisory.upgrade_command}  # then re-run yoetz"
        elif advisory is not None and advisory.outcome == "skipped_unavailable":
            package_state = LayerState.UNPROVEN
            package_detail = f"{__version__} (could not check for updates)"
        entries.append(
            DoctorEntry(
                "package",
                "Yoetz version",
                package_state,
                detail=package_detail,
                remediation=package_remediation,
            )
        )
        snapshot = await self.status_snapshot()
        remediation = {
            "harness_detected": "install Codex, or use Yoetz locally with /check",
            "mcp_verified": "run /connect to inspect or repair the integration",
            "plugin_installed": "run /connect and choose Repair",
            "service_reachable": "start the service with 'yoetz service run'",
            "vault_ready": "run /service and choose Unlock",
            "provider_binding": "run /provider to choose a provider and model",
            "credential_stored": "run /provider to store an API key",
            "privacy_permission": "run /privacy if you want external review",
        }
        for layer in snapshot.layers:
            entries.append(
                DoctorEntry(
                    layer.key,
                    layer.label,
                    layer.state,
                    detail=layer.detail,
                    remediation=remediation.get(layer.key, "")
                    if layer.state in {LayerState.BLOCKED, LayerState.NOT_CONFIGURED}
                    else "",
                )
            )
        return DoctorReport(entries=tuple(entries))

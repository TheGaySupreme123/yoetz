"""A runtime double for driving the terminal UI without touching real services.

Every method mirrors :class:`yoetz.tui.runtime.YoetzRuntime` and records what the
interface asked for, so interaction tests can assert on the *decisions* the UI
made — which plan it applied, whether it asked for a credential at all — without
a Codex installation, a running service, or a vault.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from yoetz.tui.models import (
    CheckMode,
    Detection,
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
from yoetz.tui.runtime import RuntimeError_

DESKTOP = HarnessOption(
    executable_path="/Applications/Codex.app/Contents/MacOS/codex",
    reported_version="0.44",
    label="Codex Desktop 0.44",
    description="Application installation · recommended",
    recommended=True,
)

CLI = HarnessOption(
    executable_path="/usr/local/bin/codex",
    reported_version="0.44",
    label="Codex CLI 0.44",
    description="Command-line installation",
)

PLAN = IntegrationPlan(
    harness_label="Codex Desktop",
    executable_path=DESKTOP.executable_path,
    reported_version="0.44",
    project_root="/tmp/project",
    mcp_command="yoetz mcp serve",
    mcp_server_name="yoetz",
    policy_digest="7f8a92bd",
    planned_check_ids=("lint",),
    planned_file_count=6,
    managed_paths=("/tmp/project/.agents/plugins/yoetz",),
    state_before="absent",
    already_registered=False,
    foreign_entry=False,
    preview_digest="sha256:abc123",
)

LOCAL_ONLY = PrivacyPosture(profile="local_only", llm_inference_enabled=False, readable=True)
# No external provider is bound by default, so the recommendation is Private — the same rule
# `yoetz.cli.privacy_setup.recommended_privacy_recipe` applies.
RECOMMEND_PRIVATE = PrivacyRecommendation(
    "private",
    "No external provider is configured, so this keeps network egress off entirely.",
    "In exchange, there is no external semantic review at all.",
)
# Verbatim from `YoetzRuntime.privacy_recommendation`. A fake that paraphrases lets a test
# assert wording the product does not actually say.
RECOMMEND_METADATA_ONLY = PrivacyRecommendation(
    "metadata_only",
    "It enables semantic review while disclosing the least that still works, and asks before "
    "every provider request.",
    "In exchange, the reviewer sees structural metadata only, so it cannot judge whether a "
    "claim is actually supported.",
)

UNBOUND_PROVIDER = ProviderPosture(
    endpoint_bound=False,
    provider_id=None,
    model=None,
    endpoint_profile_id=None,
    credential_connected=None,
    llm_inference_enabled=False,
    semantic_enabled=False,
    semantic_ready=False,
    readiness_determinable=False,
)


@dataclass
class FakeRuntime:
    """Records interface decisions; never performs a real operation."""

    harnesses: tuple[HarnessOption, ...] = (DESKTOP,)
    plan: IntegrationPlan = PLAN
    mcp: str = "absent"
    secure_storage: bool = True
    privacy: PrivacyPosture = LOCAL_ONLY
    recommendation: PrivacyRecommendation = RECOMMEND_METADATA_ONLY
    provider: ProviderPosture = UNBOUND_PROVIDER
    vault: VaultPosture = VaultPosture(reachable=True, state="ready", vault_mode="os_keyring")
    plan_error: RuntimeError_ | None = None
    apply_result: IntegrationOutcome | None = None
    credential_result: str | None = "stored"

    applied: list[tuple[str, str | None]] = field(default_factory=lambda: [])
    ceremonies: list[str] = field(default_factory=lambda: [])
    bindings: list[tuple[str, str]] = field(default_factory=lambda: [])
    checks: list[tuple[str, CheckMode]] = field(default_factory=lambda: [])
    opened: list[str] = field(default_factory=lambda: [])

    def project_root(self) -> Path:
        return Path("/tmp/project")

    @property
    def cwd(self) -> Path:
        return Path("/tmp/project")

    @property
    def opened_titles(self) -> tuple[str, ...]:
        return tuple(reversed(self.opened))

    def discover_harnesses(self) -> tuple[HarnessOption, ...]:
        return self.harnesses

    async def detect(self) -> Detection:
        return Detection(
            project_root="/tmp/project",
            project_name="project",
            is_git_repository=True,
            launched_from_subdirectory=False,
            harnesses=self.harnesses,
            secure_storage_available=self.secure_storage,
            already_connected=self.mcp == "yoetz_owned",
            cwd="/tmp/project",
        )

    async def mcp_state(self, option: HarnessOption) -> str:
        return self.mcp

    async def run_privacy_setup(
        self, recipe_hint: str | None, *, offer_recommended: bool = False
    ) -> object:
        from yoetz.cli.privacy_setup import PrivacySetupReport

        # ``None`` means the CLI opens on its recommendation; the fake resolves it the same way
        # the real one does so the interface cannot be tested against a rule of its own.
        recipe = recipe_hint or self.recommendation.recipe
        self.ceremonies.append(f"privacy:{recipe}{'+recommended' if offer_recommended else ''}")
        profile = {
            "private": "local_only",
            "metadata_only": "confirm_every_request",
            "assisted_review": "minimal_external",
            "expanded_review": "trusted_provider",
            "custom": "minimal_external",
        }[recipe]
        self.privacy = PrivacyPosture(
            profile=profile,
            llm_inference_enabled=recipe != "private",
            readable=True,
        )
        return PrivacySetupReport("configured", profile)

    def privacy_recommendation(self) -> PrivacyRecommendation:
        return self.recommendation

    async def integration_plan(self, option: HarnessOption) -> IntegrationPlan:
        if self.plan_error is not None:
            raise self.plan_error
        return replace(
            self.plan,
            harness_label=option.label,
            executable_path=option.executable_path,
            reported_version=option.reported_version,
        )

    async def apply_integration(
        self, option: HarnessOption, plan: IntegrationPlan
    ) -> IntegrationOutcome:
        self.applied.append((plan.preview_digest, plan.policy_digest))
        if self.apply_result is not None:
            return self.apply_result
        return IntegrationOutcome(
            outcome="registered",
            reason=None,
            layers=(
                ReadinessLayer("mcp_registered", "MCP registered", LayerState.VERIFIED),
                ReadinessLayer("mcp_verified", "MCP verified", LayerState.VERIFIED),
                ReadinessLayer("plugin_installed", "Guidance installed", LayerState.VERIFIED),
            ),
        )

    async def foreign_entry_detail(self, option: HarnessOption) -> tuple[str, ...]:
        return ('An MCP entry named "yoetz" already exists.',)

    async def vault_posture(self) -> VaultPosture:
        return self.vault

    async def privacy_posture(self) -> PrivacyPosture:
        return self.privacy

    async def provider_posture(self) -> ProviderPosture:
        return self.provider

    def provider_options(self) -> tuple[ProviderOption, ...]:
        return (
            ProviderOption(
                choice="official_openai",
                label="OpenAI",
                provider_id="openai",
                host="api.openai.com",
                base_path_prefix="/v1",
                default_model="gpt-4.1-mini",
                api_style="responses",
                endpoint_profile_id="openai-responses",
                endpoint_profile_version="1.0.0",
            ),
            ProviderOption(
                choice="anthropic",
                label="Anthropic",
                provider_id="anthropic",
                host="api.anthropic.com",
                base_path_prefix="/v1",
                default_model="claude-sonnet-4-6",
                api_style="chat_completions",
                endpoint_profile_id="anthropic-openai-chat-completions",
                endpoint_profile_version="1.0.0",
            ),
        )

    def save_provider_binding(
        self, option: ProviderOption, model: str, *, https_origin: str | None = None
    ) -> None:
        self.bindings.append((option.choice, model))

    async def store_provider_credential(self) -> str:
        self.ceremonies.append("provider_credential")
        if self.credential_result is None:
            raise RuntimeError_("cancelled", "the credential ceremony did not complete")
        if self.bindings:
            choice, model = self.bindings[-1]
            option = next(item for item in self.provider_options() if item.choice == choice)
            self.provider = ProviderPosture(
                endpoint_bound=True,
                provider_id=option.provider_id,
                model=model,
                endpoint_profile_id=option.endpoint_profile_id,
                credential_connected=True,
                llm_inference_enabled=False,
                semantic_enabled=True,
                semantic_ready=False,
                readiness_determinable=True,
            )
        return self.credential_result

    async def initialize_passphrase_vault(self) -> None:
        self.ceremonies.append("initialize_vault")

    async def initialize_system_keyring(self) -> None:
        self.ceremonies.append("initialize_keyring")

    async def unlock_vault(self) -> None:
        self.ceremonies.append("unlock_vault")

    async def service_lock(self) -> str:
        return "locked"

    async def service_stop(self) -> str:
        return "draining"

    async def status_snapshot(self) -> StatusSnapshot:
        return StatusSnapshot(
            project_root="/tmp/project",
            layers=(
                ReadinessLayer("harness_detected", "Harness detected", LayerState.VERIFIED),
                ReadinessLayer("mcp_verified", "MCP verified", LayerState.VERIFIED),
                ReadinessLayer("service_reachable", "Local service reachable", LayerState.VERIFIED),
                ReadinessLayer("vault_ready", "Vault ready", LayerState.VERIFIED),
                ReadinessLayer("local_checks", "Local deterministic checks", LayerState.VERIFIED),
                ReadinessLayer(
                    "semantic_review_ready", "Deeper review ready", LayerState.NOT_CONFIGURED
                ),
            ),
            privacy=self.privacy,
            open_work=1,
            open_findings=2,
            work_readable=True,
        )

    async def doctor(self) -> DoctorReport:
        return DoctorReport(entries=())

    async def open_task(self, title: str) -> WorkDetail:
        self.opened.append(title)
        return WorkDetail(
            item=WorkItem(
                subject_id="task_01",
                title=title,
                state="current",
                open_findings=0,
                last_check="not run",
                updated="1",
            )
        )

    async def run_check(self, title: str, mode: CheckMode) -> tuple[str, tuple[str, ...]]:
        self.checks.append((title, mode))
        return "pass", ("Verdict: pass",)

    async def build_receipt(self, title: str, output_format: str) -> ReceiptSummary:
        return ReceiptSummary(subject_id="task_01", verdict="no_unresolved_deterministic_findings")

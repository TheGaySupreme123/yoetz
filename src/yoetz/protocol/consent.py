"""Versioned agent-safe consent catalog, pending, status, and result contracts."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "AgentSafePendingModel",
    "ConsentCatalogModel",
    "ConsentCatalogOperationModel",
    "ConsentPrepareResultModel",
    "ConsentReviewResultModel",
    "ConsentStatusModel",
]

type ConsentOperation = Literal[
    "vault_initialize",
    "provider_credential_set",
    "provider_credential_rotate",
    "idle_relock_disable",
    "privacy_policy_widen",
    "backup_execute",
    "restore_execute",
    "migrate_execute",
    "skill_install",
    "harness_mcp_register",
]
type RiskClass = Literal[
    "default_safe",
    "secret_ingress",
    "secret_reauth",
    "review_only",
    "privacy_widen",
]
type Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
type PendingId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type BoundedText = Annotated[str, Field(min_length=1, max_length=2048)]

_CLOSED_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)


class _ClosedModel(BaseModel):
    model_config = _CLOSED_CONFIG


class AgentSafePendingModel(_ClosedModel):
    schema_: Literal["yoetz.consent.pending-agent/2"] = Field(alias="schema")
    operation: ConsentOperation
    risk_class: RiskClass
    pending_id: PendingId
    danger_digest: Digest
    danger_text: BoundedText
    expires_at_unix: Annotated[int, Field(gt=0)]
    target_digest: Digest
    review_command: tuple[Literal["yoetz"], Literal["consent"], Literal["review"]]

    @field_validator("review_command", mode="before")
    @classmethod
    def _adapt_review_command(cls, value: object) -> object:
        return tuple(cast(list[object], value)) if type(value) is list else value


class ConsentCatalogOperationModel(_ClosedModel):
    operation: ConsentOperation
    risk_class: RiskClass
    summary: BoundedText
    implemented: bool
    requires_provider_binding: bool
    requires_target_digest_arg: bool
    prepare_hint: BoundedText


class ConsentRulesModel(_ClosedModel):
    never_over_chat_or_mcp: tuple[
        Literal["mcp"],
        Literal["argv"],
        Literal["env"],
        Literal["stdin"],
        Literal["config"],
        Literal["transcript"],
    ]
    no_standing_yolo: Literal[True]
    path_safety_not_waivable_by_consent: Literal[True]
    trusted_console_review_only: Literal[True]
    one_pending_at_a_time: Literal[True]
    approval_arguments_forbidden: Literal[True]
    agent_selected_initialization_secret_forbidden: Literal[True]

    @field_validator("never_over_chat_or_mcp", mode="before")
    @classmethod
    def _adapt_never_channels(cls, value: object) -> object:
        return tuple(cast(list[object], value)) if type(value) is list else value


class ConsentCatalogModel(_ClosedModel):
    schema_: Literal["yoetz.consent.catalog/2"] = Field(alias="schema")
    default_safe: tuple[
        Literal["mcp.start"],
        Literal["mcp.publish_work"],
        Literal["mcp.check"],
        Literal["mcp.respond"],
        Literal["mcp.status"],
        Literal["mcp.receipt"],
        Literal["privacy.tighten"],
    ]
    rules: ConsentRulesModel
    operations: tuple[ConsentCatalogOperationModel, ...]

    @field_validator("default_safe", "operations", mode="before")
    @classmethod
    def _adapt_catalog_arrays(cls, value: object) -> object:
        return tuple(cast(list[object], value)) if type(value) is list else value


class ConsentStatusModel(_ClosedModel):
    schema_: Literal["yoetz.elevated-bootstrap.status/2"] = Field(alias="schema")
    pending: AgentSafePendingModel | None
    consent_catalog: ConsentCatalogModel


class ConsentPrepareResultModel(_ClosedModel):
    schema_: Literal["yoetz.elevated-bootstrap.prepare-result/2"] = Field(alias="schema")
    pending: AgentSafePendingModel


class ConsentReviewResultModel(_ClosedModel):
    schema_: Literal["yoetz.elevated-bootstrap.result/2"] = Field(alias="schema")
    pending_id: PendingId
    operation: ConsentOperation
    risk_class: RiskClass
    outcome: Literal["completed", "denied"]
    danger_digest: Digest
    result: dict[str, str | int]

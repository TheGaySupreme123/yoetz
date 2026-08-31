"""Versioned agent-safe consent catalog, pending, status, and result contracts."""

from __future__ import annotations

from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AgentSafePendingModel",
    "ConsentCatalogModel",
    "ConsentCatalogOperationModel",
    "ConsentPrepareResultModel",
    "ConsentReviewResultModel",
    "ConsentStatusModel",
    "RepositoryPrivacyRecipe",
]

type ConsentOperation = Literal[
    "vault_initialize",
    "vault_passphrase_rotate",
    "provider_credential_set",
    "provider_credential_rotate",
    "repository_privacy_grant",
    "idle_relock_disable",
    "privacy_policy_widen",
    "backup_execute",
    "restore_execute",
    "migrate_execute",
    "skill_install",
    "plugin_artifact_apply",
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
type RepositoryPrivacyRecipe = Literal["assisted_review", "private", "metadata_only"]

_CLOSED_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)


class _ClosedModel(BaseModel):
    model_config = _CLOSED_CONFIG


class AgentSafePendingModel(_ClosedModel):
    schema_: Literal["yoetz.consent.pending-agent/4"] = Field(alias="schema")
    operation: ConsentOperation
    risk_class: RiskClass
    pending_id: PendingId
    danger_digest: Digest
    danger_text: BoundedText
    expires_at_unix: Annotated[int, Field(gt=0)]
    target_digest: Digest
    repository_privacy_recipe: RepositoryPrivacyRecipe | None
    review_command: tuple[Literal["yoetz"], Literal["consent"], Literal["review"]]
    authorize_command: tuple[Literal["yoetz"], Literal["consent"], Literal["authorize"]] | None

    @field_validator("review_command", "authorize_command", mode="before")
    @classmethod
    def _adapt_commands(cls, value: object) -> object:
        return tuple(cast(list[object], value)) if type(value) is list else value


class ConsentCatalogOperationModel(_ClosedModel):
    operation: ConsentOperation
    risk_class: RiskClass
    summary: BoundedText
    implemented: bool
    requires_provider_binding: bool
    requires_grant_binding: bool
    requires_target_digest_arg: bool
    agent_chat_authorize_allowed: bool
    prepare_hint: BoundedText


class ConsentRulesModel(_ClosedModel):
    forbidden_secret_channels: tuple[
        Literal["mcp"],
        Literal["argv"],
        Literal["env"],
        Literal["config"],
        Literal["transcript"],
    ]
    no_standing_yolo: Literal[True]
    path_safety_not_waivable_by_consent: Literal[True]
    independent_user_presence_required_for_agent_chat: Literal[False]
    trusted_console_is_not_authority: Literal[True]
    one_pending_at_a_time: Literal[True]
    approval_arguments_forbidden: Literal[True]
    agent_selected_initialization_secret_forbidden: Literal[True]
    authorized_one_shot_stdin_permitted: Literal[True]
    agent_attested_current_chat_instruction_permitted: Literal[True]
    agent_attestation_is_independent_proof: Literal[False]
    compromised_agent_can_forge_attestation: Literal[True]

    @field_validator("forbidden_secret_channels", mode="before")
    @classmethod
    def _adapt_never_channels(cls, value: object) -> object:
        return tuple(cast(list[object], value)) if type(value) is list else value


class ConsentCatalogModel(_ClosedModel):
    schema_: Literal["yoetz.consent.catalog/4"] = Field(alias="schema")
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
    schema_: Literal["yoetz.elevated-bootstrap.status/4"] = Field(alias="schema")
    pending: AgentSafePendingModel | None
    consent_catalog: ConsentCatalogModel


class ConsentPrepareResultModel(_ClosedModel):
    schema_: Literal["yoetz.elevated-bootstrap.prepare-result/4"] = Field(alias="schema")
    pending: AgentSafePendingModel


class ConsentDeniedResultModel(_ClosedModel):
    decision: Literal["denied"]


class ConsentVaultInitializedResultModel(_ClosedModel):
    state: Literal["ready"]
    reason: Literal["succeeded"]


class ConsentProviderCredentialResultModel(_ClosedModel):
    action: Literal["set", "rotate"]
    generation: Annotated[int, Field(gt=0)]
    outcome: Literal["active", "local_only", "stored"]


class ConsentRepositoryPrivacyGrantResultModel(_ClosedModel):
    recipe: Literal["assisted_review", "private", "metadata_only"]
    outcome: Literal["granted", "tightened", "denied"]


class ConsentReviewResultModel(_ClosedModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "operation": {
                            "enum": [
                                "vault_initialize",
                                "vault_passphrase_rotate",
                                "provider_credential_set",
                                "provider_credential_rotate",
                                "repository_privacy_grant",
                            ]
                        },
                        "risk_class": {
                            "enum": ["privacy_widen", "secret_ingress", "secret_reauth"]
                        },
                        "outcome": {"const": "denied"},
                        "result": {
                            "type": "object",
                            "properties": {"decision": {"const": "denied"}},
                            "required": ["decision"],
                        },
                    }
                },
                {
                    "properties": {
                        "operation": {"const": "vault_initialize"},
                        "risk_class": {"const": "secret_ingress"},
                        "outcome": {"const": "completed"},
                        "result": {
                            "type": "object",
                            "properties": {
                                "state": {"const": "ready"},
                                "reason": {"const": "succeeded"},
                            },
                            "required": ["state", "reason"],
                        },
                    }
                },
                {
                    "properties": {
                        "operation": {"const": "vault_passphrase_rotate"},
                        "risk_class": {"const": "secret_reauth"},
                        "outcome": {"const": "completed"},
                        "result": {
                            "type": "object",
                            "properties": {
                                "state": {"const": "ready"},
                                "reason": {"const": "succeeded"},
                            },
                            "required": ["state", "reason"],
                        },
                    }
                },
                {
                    "properties": {
                        "operation": {"const": "provider_credential_set"},
                        "risk_class": {"const": "secret_ingress"},
                        "outcome": {"const": "completed"},
                        "result": {
                            "type": "object",
                            "properties": {
                                "action": {"const": "set"},
                                "generation": {"type": "integer", "exclusiveMinimum": 0},
                                "outcome": {"enum": ["active", "local_only", "stored"]},
                            },
                            "required": ["action", "generation", "outcome"],
                            "additionalProperties": False,
                        },
                    }
                },
                {
                    "properties": {
                        "operation": {"const": "provider_credential_rotate"},
                        "risk_class": {"const": "secret_ingress"},
                        "outcome": {"const": "completed"},
                        "result": {
                            "type": "object",
                            "properties": {
                                "action": {"const": "rotate"},
                                "generation": {"type": "integer", "exclusiveMinimum": 0},
                                "outcome": {"enum": ["active", "local_only", "stored"]},
                            },
                            "required": ["action", "generation", "outcome"],
                            "additionalProperties": False,
                        },
                    }
                },
                {
                    "properties": {
                        "operation": {"const": "repository_privacy_grant"},
                        "risk_class": {"const": "privacy_widen"},
                        "outcome": {"const": "completed"},
                        "result": {
                            "type": "object",
                            "properties": {
                                "recipe": {"enum": ["assisted_review", "private", "metadata_only"]},
                                "outcome": {"enum": ["granted", "tightened"]},
                            },
                            "required": ["recipe", "outcome"],
                        },
                    }
                },
            ],
            "allOf": [
                {
                    "if": {"properties": {"operation": {"const": "vault_initialize"}}},
                    "then": {
                        "properties": {
                            "risk_class": {"const": "secret_ingress"},
                            "authority_channel": {
                                "enum": [
                                    "trusted_console_presence",
                                    "agent_attested_chat_instruction",
                                ]
                            },
                        }
                    },
                },
                {
                    "if": {"properties": {"operation": {"const": "vault_passphrase_rotate"}}},
                    "then": {
                        "properties": {
                            "risk_class": {"const": "secret_reauth"},
                            "authority_channel": {
                                "enum": [
                                    "trusted_console_presence",
                                    "agent_attested_chat_instruction",
                                ]
                            },
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "operation": {
                                "enum": [
                                    "provider_credential_set",
                                    "provider_credential_rotate",
                                ]
                            }
                        }
                    },
                    "then": {
                        "properties": {
                            "risk_class": {"const": "secret_ingress"},
                            "authority_channel": {
                                "enum": [
                                    "trusted_console_presence",
                                    "agent_attested_chat_instruction",
                                ]
                            },
                        }
                    },
                },
                {
                    "if": {"properties": {"operation": {"const": "repository_privacy_grant"}}},
                    "then": {
                        "properties": {
                            "risk_class": {"const": "privacy_widen"},
                            "authority_channel": {"const": "agent_attested_chat_instruction"},
                        }
                    },
                },
            ],
        },
    )

    schema_: Literal["yoetz.elevated-bootstrap.result/4"] = Field(alias="schema")
    pending_id: PendingId
    operation: ConsentOperation
    risk_class: RiskClass
    outcome: Literal["completed", "denied"]
    danger_digest: Digest
    authority_channel: Literal["trusted_console_presence", "agent_attested_chat_instruction"]
    result: (
        ConsentDeniedResultModel
        | ConsentVaultInitializedResultModel
        | ConsentProviderCredentialResultModel
        | ConsentRepositoryPrivacyGrantResultModel
    )

    @model_validator(mode="after")
    def _bind_result_to_operation_and_outcome(self) -> Self:
        if self.operation not in {
            "vault_initialize",
            "vault_passphrase_rotate",
            "provider_credential_set",
            "provider_credential_rotate",
            "repository_privacy_grant",
        }:
            raise ValueError("review_operation_not_implemented")
        expected_risk = (
            "privacy_widen"
            if self.operation == "repository_privacy_grant"
            else "secret_reauth"
            if self.operation == "vault_passphrase_rotate"
            else "secret_ingress"
        )
        if self.risk_class != expected_risk:
            raise ValueError("review_risk_class_mismatch")
        if self.authority_channel == "agent_attested_chat_instruction" and self.operation not in {
            "vault_initialize",
            "vault_passphrase_rotate",
            "provider_credential_set",
            "provider_credential_rotate",
            "repository_privacy_grant",
        }:
            raise ValueError("review_authority_channel_mismatch")
        if (
            self.operation == "repository_privacy_grant"
            and self.authority_channel != "agent_attested_chat_instruction"
        ):
            raise ValueError("review_authority_channel_mismatch")
        if self.outcome == "denied":
            if type(self.result) is not ConsentDeniedResultModel:
                raise ValueError("review_result_outcome_mismatch")
            return self
        if self.operation in {"vault_initialize", "vault_passphrase_rotate"}:
            if type(self.result) is not ConsentVaultInitializedResultModel:
                raise ValueError("review_result_operation_mismatch")
            return self
        if self.operation == "repository_privacy_grant":
            if type(self.result) is not ConsentRepositoryPrivacyGrantResultModel:
                raise ValueError("review_result_operation_mismatch")
            if self.result.outcome == "denied":
                raise ValueError("review_result_outcome_mismatch")
            return self
        if type(self.result) is not ConsentProviderCredentialResultModel:
            raise ValueError("review_result_operation_mismatch")
        expected_action = "set" if self.operation == "provider_credential_set" else "rotate"
        if self.result.action != expected_action:
            raise ValueError("review_result_action_mismatch")
        return self

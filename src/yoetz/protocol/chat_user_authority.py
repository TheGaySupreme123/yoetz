"""Chat-user authority attestation for agent-mediated setup (issue #164)."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "CHAT_USER_AUTHORITY_CLIENTS",
    "ChatUserAttestationModel",
    "chat_user_authority_available",
]

type Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
type PendingId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type ChatUserClientKind = Literal["codex"]
type ChatUserHostCapability = Literal["required_and_active"]

# First-party hosts that advertise host-tool-approval for exact pending authorize.
CHAT_USER_AUTHORITY_CLIENTS: Final[frozenset[str]] = frozenset({"codex"})

_CLOSED_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)


class ChatUserAttestationModel(BaseModel):
    """Exact action-bound host-tool-approval envelope; never a reusable grant."""

    model_config = _CLOSED_CONFIG

    schema_: Literal["yoetz.chat-user-attestation/1"] = Field(alias="schema")
    channel: Literal["host_tool_approval"]
    client_kind: ChatUserClientKind
    host_tool_approval: ChatUserHostCapability
    pending_id: PendingId
    operation: str
    danger_digest: Digest
    target_digest: Digest
    warning_acknowledged: bool
    decision: Literal["approve", "deny"]

    @field_validator("operation")
    @classmethod
    def _operation_nonempty(cls, value: object) -> str:
        if type(value) is not str or not value or len(value) > 64:
            raise ValueError("chat_user_operation_invalid")
        return value


def chat_user_authority_available(client_kind: str, host_tool_approval: str) -> bool:
    """True when the named first-party host may use chat-user authorize."""

    return (
        type(client_kind) is str
        and client_kind in CHAT_USER_AUTHORITY_CLIENTS
        and host_tool_approval == "required_and_active"
    )

"""Service-internal application contracts and use-case coordination.

Re-exports resolve lazily (PEP 562). Importing any one submodule used to drag
the whole package surface — publish_work → unit_of_work → ports.ledger →
domain.events → protocol.models — costing ~193 ms on every Codex hook, which
consumes none of these names (#242).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from yoetz.application.publish_work import PublishWorkInternalResult
    from yoetz.application.receipt import ReceiptInternalResult
    from yoetz.application.respond import RespondInternalResult
    from yoetz.application.service import (
        Application,
        ClientProjectionContext,
        ControlProjectionBinding,
        ProjectedControlBody,
        ProjectionBindingFacts,
        ProjectionRenderMode,
        ReadyApplicationFactory,
        ServiceReadyContext,
        UnprojectedControlBody,
        VerificationPolicy,
        resolve_client_disclosure_sink,
    )
    from yoetz.application.start import StartInternalResult
    from yoetz.application.status import StatusInternalResult

__all__ = [
    "Application",
    "ClientProjectionContext",
    "ControlProjectionBinding",
    "ProjectedControlBody",
    "ProjectionBindingFacts",
    "ProjectionRenderMode",
    "PublishWorkInternalResult",
    "ReadyApplicationFactory",
    "ReceiptInternalResult",
    "RespondInternalResult",
    "ServiceReadyContext",
    "StartInternalResult",
    "StatusInternalResult",
    "UnprojectedControlBody",
    "VerificationPolicy",
    "resolve_client_disclosure_sink",
]

_LAZY: Final[dict[str, str]] = {
    "Application": "yoetz.application.service",
    "ClientProjectionContext": "yoetz.application.service",
    "ControlProjectionBinding": "yoetz.application.service",
    "ProjectedControlBody": "yoetz.application.service",
    "ProjectionBindingFacts": "yoetz.application.service",
    "ProjectionRenderMode": "yoetz.application.service",
    "PublishWorkInternalResult": "yoetz.application.publish_work",
    "ReadyApplicationFactory": "yoetz.application.service",
    "ReceiptInternalResult": "yoetz.application.receipt",
    "RespondInternalResult": "yoetz.application.respond",
    "ServiceReadyContext": "yoetz.application.service",
    "StartInternalResult": "yoetz.application.start",
    "StatusInternalResult": "yoetz.application.status",
    "UnprojectedControlBody": "yoetz.application.service",
    "VerificationPolicy": "yoetz.application.service",
    "resolve_client_disclosure_sink": "yoetz.application.service",
}


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})

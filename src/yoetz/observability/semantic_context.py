"""Task-local join from privacy/provider diagnostics to the owning check request."""

from contextvars import ContextVar

semantic_check_request: ContextVar[str | None] = ContextVar("semantic_check_request", default=None)

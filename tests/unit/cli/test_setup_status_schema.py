"""Schema and golden-vector checks for the read-only setup status envelope."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.schemas import validate_schema_instance


def test_setup_status_golden_vector_matches_schema() -> None:
    root = Path(__file__).resolve().parents[3]
    payload = json.loads((root / "fixtures/integrations/setup-status.case.json").read_bytes())
    validate_schema_instance("setup-status", "2.0.0", payload)


def test_setup_status_schema_rejects_unknown_envelope_fields() -> None:
    root = Path(__file__).resolve().parents[3]
    payload = json.loads((root / "fixtures/integrations/setup-status.case.json").read_bytes())
    payload["unexpected"] = True
    with pytest.raises(ProtocolValueError, match="schema_instance_invalid"):
        validate_schema_instance("setup-status", "2.0.0", payload)

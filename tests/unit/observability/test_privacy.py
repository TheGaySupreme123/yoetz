from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

import pytest

from yoetz.observability.privacy import (
    PRIVACY_REQUEST_BODY_DOMAIN,
    SESSION_HASH_DOMAIN,
    DiagnosticRedactionProfile,
    PrivacyFenceError,
    Sensitivity,
    assert_plaintext_safe,
    build_diagnostic_manifest,
    privacy_request_commitment,
    redact_diagnostic_record,
    redact_diagnostic_value,
    scan_for_sensitive_content,
    session_id_hash,
)
from yoetz.ports.keys import MacKeyHandle

_SESSION_ID = "ses_11111111-1111-4111-8111-111111111111"
_REQUEST_ID = "req_22222222-2222-4222-8222-222222222222"
_CORRELATION_ID = "err_33333333-3333-4333-8333-333333333333"
_CANARY = b"unique-binary-canary-\x00-credential"


def _pem_begin_marker(label: bytes) -> bytes:
    return b"-" * 5 + b"BEGIN " + label + b"-" * 5


@dataclass(frozen=True, slots=True)
class _PurposeMac:
    key: bytes
    domain: bytes

    def mac(self, domain: bytes, message: bytes) -> str:
        if domain != self.domain:
            raise ValueError("mac_domain_forbidden")
        digest = hmac.new(self.key, domain + message, hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"


def _mac(key: bytes, domain: bytes) -> MacKeyHandle:
    return _PurposeMac(key=key, domain=domain)


def test_session_id_hash_is_separate_from_plain_id() -> None:
    key = b"installation-one-log-key-32byte"
    handle = _mac(key, SESSION_HASH_DOMAIN)
    actual = session_id_hash(_SESSION_ID, handle)
    expected = hmac.new(
        key,
        SESSION_HASH_DOMAIN + _SESSION_ID.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    assert actual == f"hmac-sha256:{expected}"
    assert _SESSION_ID not in actual
    assert actual == session_id_hash(_SESSION_ID, handle)
    assert actual != session_id_hash(
        _SESSION_ID,
        _mac(b"installation-two-log-key-32byte", SESSION_HASH_DOMAIN),
    )


class _HostileText:
    def __str__(self) -> str:
        raise AssertionError("hostile text stringified")

    def __repr__(self) -> str:
        raise AssertionError("hostile text represented")


def test_redaction_helpers_strip_sensitive_text() -> None:
    record = redact_diagnostic_record(
        {
            "request_id": _REQUEST_ID,
            "correlation_id": _CORRELATION_ID,
            "duration_ms": 12,
            "outcome": "completed",
            "payload": "private task text",
            "path": "/private/repository/name",
            "credential": "sk-example",
            "message": _HostileText(),
            "unknown": _HostileText(),
        }
    )
    assert record == {
        "request_id": _REQUEST_ID,
        "correlation_id": _CORRELATION_ID,
        "duration_ms": 12,
        "outcome": "completed",
    }
    assert redact_diagnostic_value("component", _HostileText()) == "unavailable"
    assert redact_diagnostic_value("payload", _HostileText()) is None


def test_canary_checks_are_detectable() -> None:
    data = b"prefix" + _CANARY + b"suffix"
    findings = scan_for_sensitive_content(data, canaries=(_CANARY,))
    assert len(findings) == 1
    assert findings[0].kind == "canary"
    assert findings[0].start_offset == 6
    assert findings[0].end_offset == 6 + len(_CANARY)
    assert findings[0].severity is Sensitivity.SECRET
    with pytest.raises(PrivacyFenceError) as caught:
        assert_plaintext_safe(data, "/secret/path", canaries=(_CANARY,))
    assert caught.value.reason_code == "plaintext_canary_detected"
    assert caught.value.surface == "unsafe_surface"
    assert _CANARY.decode("utf-8") not in str(caught.value)


def test_canary_spanning_scan_chunk_boundary_is_detected() -> None:
    prefix = b"x" * (65_536 - len(_CANARY) // 2)
    data = prefix + _CANARY + b"suffix"
    finding = scan_for_sensitive_content(data, canaries=(_CANARY,))[0]
    assert finding.start_offset == len(prefix)
    assert finding.end_offset == len(prefix) + len(_CANARY)


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (_pem_begin_marker(b"OPENSSH PRIVATE KEY"), "private_key_marker"),
        (b"OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456", "credential_pattern"),
        (b"https://" + b"user:password@" + b"example.invalid/resource", "credential_pattern"),
        (b"github_pat_abcdefghijklmnopqrstuvwxyz123456", "credential_pattern"),
    ],
)
def test_sensitive_scanner_positive_patterns(data: bytes, kind: str) -> None:
    assert any(finding.kind == kind for finding in scan_for_sensitive_content(data))


@pytest.mark.parametrize(
    "data",
    [
        b"https://example.invalid/resource",
        b"api_key=",
        b"random structural identifier sk-short",
        b"sha256:" + b"a" * 64,
        b"\xff\xfe\x80 ordinary invalid utf8 bytes",
    ],
)
def test_sensitive_scanner_negative_patterns(data: bytes) -> None:
    assert scan_for_sensitive_content(data) == ()


def test_privacy_helpers_are_deterministic() -> None:
    record = {
        "engine_version": "0.1.0",
        "sqlite_compile_options_ok": True,
        "operation_count": 9,
        "session_id_hash": "hmac-sha256:" + "a" * 64,
    }
    first = build_diagnostic_manifest(DiagnosticRedactionProfile.SUPPORT, record)
    second = build_diagnostic_manifest(DiagnosticRedactionProfile.SUPPORT, record)
    assert first == second
    assert first["session_id_hash"] == record["session_id_hash"]
    assert "session_id_hash" not in build_diagnostic_manifest(
        DiagnosticRedactionProfile.MINIMAL,
        record,
    )
    with pytest.raises(PrivacyFenceError, match="credential_pattern_detected"):
        build_diagnostic_manifest(
            DiagnosticRedactionProfile.RELEASE_PROBE,
            {"capability_probe_id": "sk-abcdefghijklmnopqrstuvwxyz123456"},
        )


def test_mac_helpers_require_exact_purpose_and_domain() -> None:
    log = _mac(b"log-key-32-byte-purpose-binding!", SESSION_HASH_DOMAIN)
    audit = _mac(b"audit-key-32-byte-purpose-bind", PRIVACY_REQUEST_BODY_DOMAIN)
    assert session_id_hash(_SESSION_ID, log).startswith("hmac-sha256:")
    assert privacy_request_commitment(b"{}", audit).startswith("hmac-sha256:")
    with pytest.raises(ValueError, match="mac_domain_forbidden"):
        session_id_hash(_SESSION_ID, audit)
    with pytest.raises(ValueError, match="mac_domain_forbidden"):
        privacy_request_commitment(b"{}", log)
    with pytest.raises(TypeError, match="raw_mac_key_forbidden"):
        session_id_hash(_SESSION_ID, b"raw-key")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="raw_mac_key_forbidden"):
        privacy_request_commitment(b"{}", bytearray(b"raw-key"))  # type: ignore[arg-type]


def test_request_commitment_covers_final_body_only() -> None:
    key = b"privacy-audit-body-key-32-bytes!"
    handle = _mac(key, PRIVACY_REQUEST_BODY_DOMAIN)
    body = b'{"input":"bounded final body"}'
    expected = hmac.new(
        key,
        PRIVACY_REQUEST_BODY_DOMAIN + body,
        hashlib.sha256,
    ).hexdigest()
    assert privacy_request_commitment(body, handle) == f"hmac-sha256:{expected}"
    assert privacy_request_commitment(body + b"!", handle) != f"hmac-sha256:{expected}"

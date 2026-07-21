"""Deterministic privacy fences for plaintext diagnostic surfaces."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from yoetz.domain.values import parse_rfc3339_millis, validate_commitment
from yoetz.ports.keys import MacKeyHandle
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.ids import IdKind, validate_id

__all__ = [
    "PRIVACY_REQUEST_BODY_DOMAIN",
    "SESSION_HASH_DOMAIN",
    "DiagnosticRedactionProfile",
    "PrivacyFenceError",
    "ScanFinding",
    "Sensitivity",
    "assert_plaintext_safe",
    "build_diagnostic_manifest",
    "privacy_request_commitment",
    "redact_diagnostic_record",
    "redact_diagnostic_value",
    "scan_for_sensitive_content",
    "session_id_hash",
]

SESSION_HASH_DOMAIN: Final = b"yoetz/session-log-id/v1\x00"
PRIVACY_REQUEST_BODY_DOMAIN: Final = b"yoetz/privacy-egress-request/v1\x00"

_MAX_SCAN_FINDINGS: Final = 128
_MAX_CANARIES: Final = 64
_MAX_CANARY_BYTES: Final = 4_096
_SCAN_CHUNK_BYTES: Final = 65_536
_SCAN_OVERLAP_BYTES: Final = 4_096
_MAX_STRUCTURAL_TEXT_BYTES: Final = 128
_MAX_SAFE_INTEGER: Final = 2**53 - 1

_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$", re.ASCII)
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._/+:-]{0,127}$", re.ASCII)
_HASH = re.compile(r"^(?:hmac-)?sha256:[0-9a-f]{64}$", re.ASCII)


def _pem_begin_marker(label: bytes) -> bytes:
    # Built from parts so the publication-boundary scanner does not treat detector
    # constants as embedded private-key material (PRIV-CRED-001).
    return b"-" * 5 + b"BEGIN " + label + b"-" * 5


_PRIVATE_KEY_MARKERS: Final = (
    _pem_begin_marker(b"PRIVATE KEY"),
    _pem_begin_marker(b"RSA PRIVATE KEY"),
    _pem_begin_marker(b"EC PRIVATE KEY"),
    _pem_begin_marker(b"OPENSSH PRIVATE KEY"),
)
_CREDENTIAL_PATTERNS: Final = (
    re.compile(rb"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,256}(?![A-Za-z0-9_-])"),
    re.compile(rb"(?<![A-Za-z0-9])(?:ghp_|gho_|ghu_|ghs_)[A-Za-z0-9]{20,256}"),
    re.compile(rb"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,256}"),
    re.compile(rb"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,256}"),
    re.compile(rb"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"),
)
_URI_PASSWORD = re.compile(rb"[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s/:@]{1,128}:[^\s/@]{1,256}@")
_SECRET_ASSIGNMENT = re.compile(
    rb"(?i)(?:^|[^A-Za-z0-9_])['\"]?"
    rb"(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|private[_-]?key|secret)"
    rb"['\"]?\s*[:=]\s*['\"]?[^\s,'\";}{]{1,512}"
)

_LOG_FIELDS: Final = frozenset(
    {
        "timestamp",
        "level",
        "component",
        "operation",
        "correlation_id",
        "session_id_hash",
        "request_id",
        "duration_ms",
        "outcome",
        "engine_version",
        "policy_version",
        "sqlite_source_id_hash",
    }
)
_MANIFEST_FIELDS: Final = frozenset(
    {
        "schema_version",
        "redaction_profile",
        "package_version",
        "protocol_version",
        "control_protocol_version",
        "engine_version",
        "policy_version",
        "projection_version",
        "privacy_policy_schema_version",
        "egress_receipt_schema_version",
        "platform_identity",
        "runtime_identity",
        "sqlite_version",
        "sqlite_source_id_hash",
        "sqlite_compile_options_ok",
        "startup_check_outcome",
        "startup_reason_code",
        "operation_count",
        "duration_bucket_ms",
        "terminal_outcome_count",
        "session_id_hash",
        "capability_probe_id",
    }
)
_KNOWN_FIELDS: Final = _LOG_FIELDS | _MANIFEST_FIELDS
_ALWAYS_OMITTED: Final = frozenset(
    {
        "message",
        "exception",
        "traceback",
        "stack",
        "path",
        "filename",
        "url",
        "command",
        "argv",
        "prompt",
        "payload",
        "body",
        "response",
        "sql",
        "parameters",
        "credential",
        "authorization",
        "environment",
    }
)
_TOKEN_FIELDS: Final = frozenset(
    {
        "level",
        "component",
        "operation",
        "outcome",
        "redaction_profile",
        "startup_check_outcome",
        "startup_reason_code",
        "capability_probe_id",
    }
)
_VERSION_FIELDS: Final = frozenset(
    {
        "schema_version",
        "package_version",
        "protocol_version",
        "control_protocol_version",
        "engine_version",
        "policy_version",
        "projection_version",
        "privacy_policy_schema_version",
        "egress_receipt_schema_version",
        "platform_identity",
        "runtime_identity",
        "sqlite_version",
    }
)
_INTEGER_FIELDS: Final = frozenset(
    {"duration_ms", "operation_count", "duration_bucket_ms", "terminal_outcome_count"}
)
_BOOLEAN_FIELDS: Final = frozenset({"sqlite_compile_options_ok"})

_omitted_field_count = 0


class Sensitivity(str, Enum):  # noqa: UP042 - frozen diagnostic vocabulary
    PUBLIC_STRUCTURAL = "public_structural"
    LOCAL_IDENTIFIER = "local_identifier"
    USER_CONTENT = "user_content"
    SECRET = "secret"
    KEY_MATERIAL = "key_material"


@dataclass(frozen=True, slots=True)
class ScanFinding:
    """One bounded sensitive-content match, without retaining matched bytes."""

    kind: str
    start_offset: int
    end_offset: int
    severity: Sensitivity

    def __post_init__(self) -> None:
        if self.kind not in {"canary", "credential_pattern", "private_key_marker"}:
            raise ValueError("scan_finding_kind_invalid")
        if (
            type(self.start_offset) is not int
            or type(self.end_offset) is not int
            or not 0 <= self.start_offset < self.end_offset
        ):
            raise ValueError("scan_finding_offset_invalid")
        if type(self.severity) is not Sensitivity:
            raise TypeError("scan_finding_severity_invalid")


class DiagnosticRedactionProfile(str, Enum):  # noqa: UP042 - frozen profile vocabulary
    MINIMAL = "minimal"
    SUPPORT = "support"
    RELEASE_PROBE = "release_probe"


class PrivacyFenceError(Exception):
    """A bounded privacy failure which never echoes the matched input."""

    __slots__ = ("reason_code", "surface")

    reason_code: str
    surface: str

    def __init__(self, reason_code: str, surface: str) -> None:
        if type(reason_code) is not str or _TOKEN.fullmatch(reason_code) is None:
            raise ValueError("privacy_reason_invalid")
        safe_surface = (
            surface if type(surface) is str and _TOKEN.fullmatch(surface) else "unsafe_surface"
        )
        self.reason_code = reason_code
        self.surface = safe_surface
        super().__init__(f"{reason_code}:{safe_surface}")


def _append_finding(
    findings: list[ScanFinding],
    seen: set[tuple[str, int, int]],
    kind: str,
    start: int,
    end: int,
    severity: Sensitivity,
) -> None:
    identity = (kind, start, end)
    if identity not in seen and len(findings) < _MAX_SCAN_FINDINGS:
        seen.add(identity)
        findings.append(ScanFinding(kind, start, end, severity))


def _scan_chunks(data: bytes) -> Iterator[tuple[int, bytes]]:
    if len(data) <= _SCAN_CHUNK_BYTES:
        yield 0, data
        return
    start = 0
    while start < len(data):
        end = min(len(data), start + _SCAN_CHUNK_BYTES)
        yield start, data[start:end]
        if end == len(data):
            break
        start = end - _SCAN_OVERLAP_BYTES


def scan_for_sensitive_content(
    data: bytes,
    *,
    canaries: tuple[bytes, ...] = (),
) -> tuple[ScanFinding, ...]:
    """Return bounded structural findings for known plaintext-sensitive patterns."""

    if type(data) is not bytes:
        raise TypeError("scan_data_not_bytes")
    if type(canaries) is not tuple or len(canaries) > _MAX_CANARIES:
        raise PrivacyFenceError("scanner_input_invalid", "sensitive_content_scan")
    if any(
        type(canary) is not bytes or not canary or len(canary) > _MAX_CANARY_BYTES
        for canary in canaries
    ):
        raise PrivacyFenceError("scanner_input_invalid", "sensitive_content_scan")

    findings: list[ScanFinding] = []
    seen: set[tuple[str, int, int]] = set()

    for canary in canaries:
        for chunk_start, chunk in _scan_chunks(data):
            offset = 0
            while len(findings) < _MAX_SCAN_FINDINGS:
                found = chunk.find(canary, offset)
                if found < 0:
                    break
                absolute = chunk_start + found
                _append_finding(
                    findings,
                    seen,
                    "canary",
                    absolute,
                    absolute + len(canary),
                    Sensitivity.SECRET,
                )
                offset = found + max(1, len(canary))

    for marker in _PRIVATE_KEY_MARKERS:
        for chunk_start, chunk in _scan_chunks(data):
            offset = 0
            while len(findings) < _MAX_SCAN_FINDINGS:
                found = chunk.find(marker, offset)
                if found < 0:
                    break
                absolute = chunk_start + found
                _append_finding(
                    findings,
                    seen,
                    "private_key_marker",
                    absolute,
                    absolute + len(marker),
                    Sensitivity.KEY_MATERIAL,
                )
                offset = found + len(marker)

    for pattern in (*_CREDENTIAL_PATTERNS, _URI_PASSWORD, _SECRET_ASSIGNMENT):
        for chunk_start, chunk in _scan_chunks(data):
            for match in pattern.finditer(chunk):
                _append_finding(
                    findings,
                    seen,
                    "credential_pattern",
                    chunk_start + match.start(),
                    chunk_start + match.end(),
                    Sensitivity.SECRET,
                )
                if len(findings) >= _MAX_SCAN_FINDINGS:
                    break

    findings.sort(key=lambda finding: (finding.start_offset, finding.end_offset, finding.kind))
    return tuple(findings)


def assert_plaintext_safe(
    data: bytes,
    surface: str,
    *,
    canaries: tuple[bytes, ...] = (),
) -> None:
    """Fail closed when known sensitive content is present on a plaintext surface."""

    findings = scan_for_sensitive_content(data, canaries=canaries)
    if not findings:
        return
    first = findings[0]
    reason = {
        "canary": "plaintext_canary_detected",
        "credential_pattern": "credential_pattern_detected",
        "private_key_marker": "private_key_marker_detected",
    }[first.kind]
    raise PrivacyFenceError(reason, surface)


def _safe_mac(handle: MacKeyHandle, domain: bytes, message: bytes) -> str:
    if isinstance(handle, bytes | bytearray | memoryview):
        raise TypeError("raw_mac_key_forbidden")
    try:
        result = handle.mac(domain, message)
    except AttributeError as exc:
        raise TypeError("mac_handle_required") from exc
    return validate_commitment(result)


def session_id_hash(session_id: str, log_mac: MacKeyHandle) -> str:
    """Return an installation-local opaque session correlation label."""

    validated = validate_id(IdKind.SESSION, session_id)
    return _safe_mac(log_mac, SESSION_HASH_DOMAIN, validated.encode("ascii"))


def privacy_request_commitment(final_request_body: bytes, audit_mac: MacKeyHandle) -> str:
    """Commit to the exact final provider/application body before transport metadata."""

    if type(final_request_body) is not bytes:
        raise TypeError("request_body_not_bytes")
    return _safe_mac(audit_mac, PRIVACY_REQUEST_BODY_DOMAIN, final_request_body)


def _bounded_token(value: object, pattern: re.Pattern[str]) -> str:
    if (
        type(value) is not str
        or len(value.encode("utf-8")) > _MAX_STRUCTURAL_TEXT_BYTES
        or pattern.fullmatch(value) is None
    ):
        return "unavailable"
    return value


def redact_diagnostic_value(name: str, value: object) -> JsonValue:
    """Convert one known structural diagnostic field without generic stringification."""

    if type(name) is not str or name in _ALWAYS_OMITTED or name not in _KNOWN_FIELDS:
        return None
    try:
        if name == "timestamp":
            if type(value) is not str:
                return "unavailable"
            parse_rfc3339_millis(value)
            return value
        if name == "request_id":
            return validate_id(IdKind.REQUEST, value)
        if name == "correlation_id":
            return validate_id(IdKind.CORRELATION, value)
        if name in {"session_id_hash", "sqlite_source_id_hash"}:
            return value if type(value) is str and _HASH.fullmatch(value) else "unavailable"
        if name in _TOKEN_FIELDS:
            return _bounded_token(value, _TOKEN)
        if name in _VERSION_FIELDS:
            return _bounded_token(value, _VERSION)
        if name in _INTEGER_FIELDS:
            return (
                value if type(value) is int and 0 <= value <= _MAX_SAFE_INTEGER else "unavailable"
            )
        if name in _BOOLEAN_FIELDS:
            return value if type(value) is bool else "unavailable"
    except BaseException:
        return "unavailable"
    return None


def redact_diagnostic_record(record: Mapping[str, object]) -> dict[str, JsonValue]:
    """Drop unknown fields and retain only bounded structural diagnostic values."""

    global _omitted_field_count
    output: dict[str, JsonValue] = {}
    try:
        keys = tuple(record.keys())
    except BaseException:
        return output
    for key in keys:
        if type(key) is not str or key not in _KNOWN_FIELDS or key in _ALWAYS_OMITTED:
            _omitted_field_count += 1
            continue
        try:
            output[key] = redact_diagnostic_value(key, record[key])
        except BaseException:
            output[key] = "unavailable"
    return output


def build_diagnostic_manifest(
    profile: DiagnosticRedactionProfile,
    structural_inputs: Mapping[str, object],
) -> dict[str, JsonValue]:
    """Build the sole bounded plaintext diagnostic-manifest shape."""

    if type(profile) is not DiagnosticRedactionProfile:
        raise TypeError("diagnostic_profile_invalid")
    redacted = redact_diagnostic_record(structural_inputs)
    if profile is DiagnosticRedactionProfile.MINIMAL:
        redacted.pop("session_id_hash", None)
        redacted.pop("capability_probe_id", None)
    elif profile is DiagnosticRedactionProfile.SUPPORT:
        redacted.pop("capability_probe_id", None)
    else:
        redacted.pop("session_id_hash", None)
    redacted["schema_version"] = "yoetz-diagnostic-manifest/1"
    redacted["redaction_profile"] = profile.value
    assert_plaintext_safe(canonical_encode(redacted), "diagnostic_manifest")
    return redacted

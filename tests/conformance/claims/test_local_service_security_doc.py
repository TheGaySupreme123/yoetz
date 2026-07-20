"""Claims conformance: the public local-service security explanation stays complete and bounded.

Grounded entirely in the real, already-authored ``docs/protocol/local-service-security.md`` page and
the claim map at ``docs/public-claims.json`` that binds it to evidence (per the page's own trailer:
"Every promise on this page maps to an ADR, an owning spec, and an executable test"). This file is
that executable test.

Every assertion below is a literal, normalized-whitespace substring check against the real document
text -- section headings, required terminology, and the frozen negation sentences under "What this
page must never imply" -- plus link-target existence and a cross-check that the two claims most
directly about this page (the trust-boundary claim and the keyring/presence gate claim) both name
this exact test file. Nothing here re-derives service behavior; it only locks the public explanation
against silent overclaim or omission.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC_PATH = _REPO_ROOT / "docs" / "protocol" / "local-service-security.md"
_CLAIMS_PATH = _REPO_ROOT / "docs" / "public-claims.json"
_THIS_TEST_PATH = "tests/conformance/claims/test_local_service_security_doc.py"

_REQUIRED_HEADINGS = (
    "## The binding promise",
    "## Trust boundary",
    "## Service states",
    "## Endpoints and clients",
    "## First-install: keyring versus passphrase",
    "### Existing-keyring vaults are a distinct case",
    "## Idle, session, suspend, and explicit relock",
    "## Forbidden secret surfaces",
    "## Limits of same-UID and process-memory protection",
    "## Headless and native-vault status",
    "## Diagnostics never capture raw content",
    "## Troubleshooting",
    "## What this page must never imply",
    "## See also",
    "## Claims and evidence",
)

# Required states/clients/secret-surface/limit/relock/headless/gate/distinction/reason terminology.
_REQUIRED_SUBSTRINGS = (
    "One local service owns writers, keys, credentials, and decrypted state.",
    "`ready`",
    "`locked`",
    "Peer-UID authentication",
    "SO_PEERCRED",
    "getpeereid",
    "They cannot distinguish a malicious process already running as the same account.",
    "A compromised active user account, a root process, or inspection of the ready service",
    "no perfect zero-copy or guaranteed zeroization claim is made",
    "UserPresencePort",
    "human_authority_unavailable",
    "setup required",
    "Neither branch silently falls back to the other.",
    "ready-local only",
    "current presence evidence",
    "idle_relock_policy_change",
    "900 seconds",
    "vault_initialize",
    "vault_unlock",
    "security_reauthentication",
    "no inherited file descriptor",
    "--password-fd",
    "launchd",
    "systemd-user",
    "deferred",
    "Ordinary exception handling retains only a bounded structural correlation ID",
)

# Forbidden-overclaim assertions -- the exact negation sentences from "What this page must never
# imply" must be present verbatim, proving the disclaimer is real rather than merely implied.
_REQUIRED_NEGATIONS = (
    "The OS keyring is never described as an automatic fallback for a passphrase vault, "
    "or vice versa.",
    "MCP can never unlock the service.",
    'Locked or missing data is never "deleted" or "empty."',
    "A service manager reporting the process as running never means the vault is `ready`.",
    "Encryption is never described as protecting against a compromised active account, "
    "root, or live-memory adversary.",
    "A boolean flag or TTY acknowledgment never proves human presence.",
    "Idle relock is never described as changeable through an ordinary command, config "
    "value, or MCP call",
)

# Overclaims this page must never make as an affirmative (non-negated) statement.
_FORBIDDEN_PHRASES = (
    "keyring is the default",
    "keyring is the default mode",
    "mcp can unlock",
    "mcp unlocks",
    "automatically falls back",
    "deletes the key",
    "zeroizes memory",
    "process running means the vault is ready",
    "process is running, so the vault is ready",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _document_text() -> str:
    return _DOC_PATH.read_text(encoding="utf-8")


def _load_claims() -> list[dict[str, object]]:
    document = json.loads(_CLAIMS_PATH.read_text(encoding="utf-8"))
    return cast(list[dict[str, object]], document["claims"])


def test_document_sections_and_terminology_are_complete() -> None:
    """Every required state/client/secret/limit/relock/gate/distinction section is present."""

    text = _document_text()
    normalized = _normalize(text)

    for heading in _REQUIRED_HEADINGS:
        assert heading in text, heading

    for phrase in _REQUIRED_SUBSTRINGS:
        assert _normalize(phrase) in normalized, phrase


def test_forbidden_overclaims_are_absent_and_negations_are_verbatim() -> None:
    """The page never asserts a forbidden overclaim, and its explicit denials are exact."""

    text = _document_text()
    normalized = _normalize(text)
    lowered = normalized.lower()

    for negation in _REQUIRED_NEGATIONS:
        assert _normalize(negation) in normalized, negation

    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in lowered, phrase


def test_links_resolve_and_claim_map_owns_this_test() -> None:
    """Every markdown link target exists, and the claim map names this file as owning evidence."""

    text = _document_text()
    link_targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    assert link_targets

    for target in link_targets:
        path = target.split("#", 1)[0]
        if not path:
            continue
        resolved = (_DOC_PATH.parent / path).resolve()
        assert resolved.is_file(), target

    claims = {cast(str, claim["claim_id"]): claim for claim in _load_claims()}
    doc_surface = "docs/protocol/local-service-security.md"

    trust_boundary = claims["privacy.trusted_local_service_boundary"]
    keyring_gate = claims["privacy.keyring_initialization_presence_gate"]

    for claim in (trust_boundary, keyring_gate):
        assert doc_surface in cast(list[str], claim["surfaces"])
        assert _THIS_TEST_PATH in cast(list[str], claim["tests"])

    # The page's own trailer names both its owning claim-evidence file and the claim map itself.
    assert _THIS_TEST_PATH in text
    assert "docs/public-claims.json" in text

"""Client-safe confidential channel sequencing and one-shot secret tests."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Buffer
from typing import cast

import pytest

from yoetz.adapters.control.unix_socket import AuthenticatedUnixStream
from yoetz.service import confidential_protocol
from yoetz.service.confidential_client import (
    ConfidentialClientError,
    HumanControlClient,
)
from yoetz.service.confidential_protocol import (
    ClientOpenEnvelope,
    ConfidentialSecretPurpose,
    EmptyVaultTarget,
    HumanCeremonyBinding,
    HumanCeremonyKind,
    SecretIngressBinding,
    SecretRequiredPhase,
    ServerCloseEnvelope,
    ServerOpenedEnvelope,
    ServerResultEnvelope,
    VaultInitializePreview,
    VaultStateResult,
    decode_human_frame,
    decode_secret_header,
    encode_human_frame,
    monotonic_milliseconds,
)

_SERVICE_ID = "svc_00000000-0000-4000-8000-000000000001"
_CEREMONY_ID = "1" * 64
_DIGEST = "sha256:" + "0" * 64


def _expiry() -> int:
    return monotonic_milliseconds(time.monotonic()) + 60_000


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _HumanStream:
    def __init__(self) -> None:
        self.peer_identity = object()
        self.sent: list[bytes] = []
        self._incoming = bytearray()
        self._ready = asyncio.Condition()
        self.closed = False
        self.binding: SecretIngressBinding | None = None

    async def receive(self, max_bytes: int) -> bytes:
        async with self._ready:
            await self._ready.wait_for(lambda: bool(self._incoming) or self.closed)
            if not self._incoming:
                return b""
            size = min(max_bytes, len(self._incoming))
            result = bytes(self._incoming[:size])
            del self._incoming[:size]
            return result

    async def send_all(self, data: Buffer) -> None:
        encoded = bytes(data)
        self.sent.append(encoded)
        frame = decode_human_frame(encoded)
        if type(frame) is ClientOpenEnvelope:
            binding = SecretIngressBinding(
                binding_version=1,
                ceremony_id=_CEREMONY_ID,
                secret_challenge="2" * 64,
                purpose=ConfidentialSecretPurpose.VAULT_INITIALIZE,
                service_instance_id=_SERVICE_ID,
                service_generation=1,
                vault_generation=0,
                policy_generation=None,
                target_digest=_DIGEST,
                expires_at_monotonic_ms=_expiry(),
            )
            self.binding = binding
            opened = ServerOpenedEnvelope(
                ceremony_id=_CEREMONY_ID,
                step=1,
                binding=HumanCeremonyBinding(
                    binding_version=1,
                    ceremony_id=_CEREMONY_ID,
                    connection_nonce=frame.connection_nonce,
                    ceremony_kind=frame.ceremony_kind,
                    service_instance_id=_SERVICE_ID,
                    service_generation=1,
                    vault_generation=0,
                    policy_generation=None,
                    target_digest=_DIGEST,
                    expires_at_monotonic_ms=_expiry(),
                ),
                preview=VaultInitializePreview(),
                phase=SecretRequiredPhase(binding=binding),
            )
            await self.feed(encode_human_frame(opened))

    async def feed(self, data: bytes) -> None:
        async with self._ready:
            self._incoming.extend(data)
            self._ready.notify_all()

    async def shutdown_write(self) -> None:
        return None

    async def aclose(self) -> None:
        async with self._ready:
            self.closed = True
            self._ready.notify_all()


class _SecretStream:
    def __init__(self, response: bytes = b"") -> None:
        self.peer_identity = object()
        self.sent: list[tuple[type[object], bytes]] = []
        self.response = response
        self.closed = False

    async def receive(self, max_bytes: int) -> bytes:
        response, self.response = self.response[:max_bytes], self.response[max_bytes:]
        return response

    async def send_all(self, data: Buffer) -> None:
        self.sent.append((type(data), bytes(data)))

    async def shutdown_write(self) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


async def _open(
    secret_stream: _SecretStream,
) -> tuple[HumanControlClient, _HumanStream, object]:
    human_stream = _HumanStream()

    async def connect_human() -> AuthenticatedUnixStream:
        return cast(AuthenticatedUnixStream, human_stream)

    async def connect_secret() -> AuthenticatedUnixStream:
        return cast(AuthenticatedUnixStream, secret_stream)

    factory = getattr(HumanControlClient, "_with_connectors")
    client = cast(HumanControlClient, factory(connect_human, connect_secret, timeout_seconds=1.0))
    session = await client.open(
        HumanCeremonyKind.VAULT_INITIALIZE,
        EmptyVaultTarget(expected_mode="uninitialized"),
    )
    return client, human_stream, session


@pytest.mark.anyio
async def test_live_session_token_allows_one_secret_send_and_overwrites_source() -> None:
    secret_stream = _SecretStream()
    client, human_stream, raw_session = await _open(secret_stream)
    session = raw_session
    binding = human_stream.binding
    assert binding is not None
    secret_client = getattr(session, "_secret_client")()
    token = getattr(session, "_session_token")()
    source = bytearray(b"sixteen-byte-key")
    await secret_client.send_once(binding, source, token)
    assert source == bytearray(len(source))
    assert secret_stream.sent[0][0] is bytes
    assert secret_stream.sent[1][0] is memoryview
    parsed_binding, length = decode_secret_header(secret_stream.sent[0][1])
    assert parsed_binding == binding
    assert length == len(b"sixteen-byte-key")
    with pytest.raises(TypeError, match="opaque"):
        repr(token)
    with pytest.raises(TypeError, match="not_copyable"):
        copy.copy(token)

    await human_stream.feed(
        encode_human_frame(
            ServerResultEnvelope(
                ceremony_id=_CEREMONY_ID,
                step=2,
                result=VaultStateResult(state="ready", reason="succeeded"),
            )
        )
        + encode_human_frame(
            ServerCloseEnvelope(ceremony_id=_CEREMONY_ID, step=3, outcome="completed")
        )
    )
    result = await getattr(session, "wait_phase_or_result")()
    assert result == VaultStateResult(state="ready", reason="succeeded")
    await client.close()


@pytest.mark.anyio
async def test_duplicate_or_crossed_secret_attempt_fails_and_still_overwrites() -> None:
    secret_stream = _SecretStream()
    client, human_stream, session = await _open(secret_stream)
    binding = human_stream.binding
    assert binding is not None
    secret_client = getattr(session, "_secret_client")()
    token = getattr(session, "_session_token")()
    first = bytearray(b"sixteen-byte-key")
    await secret_client.send_once(binding, first, token)
    duplicate = bytearray(b"sixteen-byte-key")
    with pytest.raises(ConfidentialClientError, match="correlation_mismatch"):
        await secret_client.send_once(binding, duplicate, token)
    assert duplicate == bytearray(len(duplicate))
    assert len(secret_stream.sent) == 2
    await client.close()


@pytest.mark.anyio
async def test_yzs_response_bytes_fail_closed_and_source_is_overwritten() -> None:
    secret_stream = _SecretStream(b"x")
    client, human_stream, session = await _open(secret_stream)
    binding = human_stream.binding
    assert binding is not None
    source = bytearray(b"sixteen-byte-key")
    with pytest.raises(ConfidentialClientError, match="response_bytes"):
        await getattr(session, "_secret_client")().send_once(
            binding,
            source,
            getattr(session, "_session_token")(),
        )
    assert source == bytearray(len(source))
    assert secret_stream.closed
    await client.close()


def test_confidential_clients_have_no_raw_endpoint_or_secret_constructor() -> None:
    with pytest.raises(TypeError, match="constructor_private"):
        getattr(
            __import__("yoetz.service.confidential_client", fromlist=["x"]),
            "ConfidentialSecretClient",
        )(lambda: None, object(), _token=None)
    assert not hasattr(HumanControlClient, "connect_secret")
    assert "HumanEnvelope" in confidential_protocol.__all__

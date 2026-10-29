"""Stream client: DTLS interop, signalling protocol, frame pipeline."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer


def test_interop_certificate_offers_both_cipher_families() -> None:
    """The robot's Linux gst build has an RSA DTLS cert; macOS ECDSA.

    aiortc's default list is ECDSA-only, which the robot answers with a
    fatal handshake_failure alert — both families must be offered.
    """
    from aiortc.rtcdtlstransport import SRTP_PROFILES
    from OpenSSL import SSL

    from custom_components.reachy_mini.stream import InteropCertificate

    cert = InteropCertificate.generate()
    ctx = cert._create_ssl_context(SRTP_PROFILES)
    ciphers = SSL.Connection(ctx).get_cipher_list()
    assert "ECDHE-RSA-AES128-GCM-SHA256" in ciphers
    assert "ECDHE-ECDSA-AES128-GCM-SHA256" in ciphers


async def test_default_pc_factory_installs_interop_certificate() -> None:
    """The interop certificate must actually end up on the connection.

    Caught live: aiortc's RTCConfiguration has no `certificates` field,
    so passing it as a kwarg raised TypeError inside the session task
    and the RSA-capable DTLS context never applied. Pin the private
    attribute injection against aiortc upgrades.
    """
    from custom_components.reachy_mini.stream import (
        InteropCertificate,
        _default_pc_factory,
    )

    pc = _default_pc_factory()
    try:
        certs = pc._RTCPeerConnection__certificates
        assert len(certs) == 1
        assert isinstance(certs[0], InteropCertificate)
    finally:
        await pc.close()


FAKE_OFFER_SDP = (
    "v=0\r\no=- 0 0 IN IP4 172.16.0.170\r\ns=-\r\nt=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 97\r\na=mid:video1\r\n"
)
# Two candidate lines: the client must trickle BOTH as `ice` messages
# because gst webrtcbin ignores candidates embedded in the SDP.
FAKE_ANSWER_SDP = (
    "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 97\r\na=mid:video1\r\n"
    "a=candidate:1 1 UDP 2113937151 127.0.0.1 50000 typ host\r\n"
    "a=candidate:2 1 UDP 2113937151 127.0.0.1 50001 typ host\r\n"
)


def make_frame():
    """A real (tiny, black) av.VideoFrame the JPEG encoder accepts."""
    import av

    frame = av.VideoFrame(16, 16, "rgb24")
    for plane in frame.planes:
        plane.update(bytes(plane.buffer_size))
    return frame


class FakeVideoTrack:
    """Stands in for aiortc's RemoteStreamTrack."""

    kind = "video"

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    async def recv(self):
        item = await self.queue.get()
        if item is None:
            from aiortc.mediastreams import MediaStreamError

            raise MediaStreamError
        return item


class FakePeerConnection:
    """Records the negotiation the stream client drives."""

    def __init__(self) -> None:
        self._handlers: dict = {}
        self.remote_description = None
        self.candidates: list = []
        self.closed = False
        self.track = FakeVideoTrack()

        class _Desc:
            sdp = FAKE_ANSWER_SDP

        self.localDescription = _Desc()

    def on(self, event, handler=None):
        if handler is None:
            def register(fn):
                self._handlers[event] = fn
                return fn

            return register
        self._handlers[event] = handler
        return handler

    async def setRemoteDescription(self, description) -> None:
        self.remote_description = description
        # aiortc fires "track" while applying the remote description.
        self._handlers["track"](self.track)

    async def createAnswer(self):
        return object()

    async def setLocalDescription(self, description) -> None:
        pass

    async def addIceCandidate(self, candidate) -> None:
        self.candidates.append(candidate)

    async def close(self) -> None:
        self.closed = True
        await self.track.queue.put(None)


class ServerScript:
    """Configurable behavior + transcript for the fake signalling server."""

    def __init__(self) -> None:
        self.behavior = "happy"  # or "no_producer" / "end_session"
        self.received: list[dict] = []


@pytest.fixture
async def signalling_server(socket_enabled: None):
    """A scripted gst-webrtc-signalling server (welcome/list/startSession).

    ``socket_enabled`` opts back into real localhost sockets: HA's test
    harness blocks them by default, but this fixture and the stream
    client under test both need a real TCP/WebSocket loopback connection.
    """
    script = ServerScript()

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        if script.behavior == "stall":
            # Never prepares the websocket: the client's ws_connect()
            # await hangs until the caller cancels it.
            await asyncio.sleep(30)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "welcome", "peerId": "consumer-1"})
        async for raw in ws:
            msg = json.loads(raw.data)
            script.received.append(msg)
            if msg["type"] == "list":
                producers = (
                    []
                    if script.behavior == "no_producer"
                    else [{"id": "prod-1", "meta": {"name": "reachymini"}}]
                )
                await ws.send_json({"type": "list", "producers": producers})
            elif msg["type"] == "startSession":
                await ws.send_json(
                    {
                        "type": "sessionStarted",
                        "peerId": msg["peerId"],
                        "sessionId": "sess-1",
                    }
                )
                await ws.send_json(
                    {
                        "type": "peer",
                        "sessionId": "sess-1",
                        "sdp": {"type": "offer", "sdp": FAKE_OFFER_SDP},
                    }
                )
            elif msg["type"] == "peer" and "sdp" in msg:
                if script.behavior == "end_session":
                    await ws.send_json({"type": "endSession", "sessionId": "sess-1"})
                else:
                    await ws.send_json(
                        {
                            "type": "peer",
                            "sessionId": "sess-1",
                            "ice": {
                                "candidate": (
                                    "candidate:1 1 UDP 2015363327 "
                                    "127.0.0.1 48642 typ host"
                                ),
                                "sdpMLineIndex": 0,
                            },
                        }
                    )
        return ws

    app = web.Application()
    app.router.add_get("/", ws_handler)
    server = TestServer(app)
    await server.start_server()
    yield script, server.port
    await server.close()


@pytest.fixture
async def http_session():
    session = aiohttp.ClientSession()
    yield session
    await session.close()


def _make_client(port, http_session, pc, **kwargs):
    from custom_components.reachy_mini.stream import ReachyMiniStreamClient

    kwargs.setdefault("idle_timeout", 0.05)
    return ReachyMiniStreamClient(
        "127.0.0.1", session=http_session, port=port,
        pc_factory=lambda: pc, **kwargs,
    )


async def _wait_for(predicate, timeout=2.0):
    """Poll until predicate() is true (condition-based waiting)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not met"
        await asyncio.sleep(0.01)


async def test_happy_path_negotiates_and_serves_jpeg(
    signalling_server, http_session
) -> None:
    script, port = signalling_server
    pc = FakePeerConnection()
    client = _make_client(port, http_session, pc)

    await client.acquire()
    await pc.track.queue.put(make_frame())
    jpeg = await client.async_get_image(timeout=5)
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")

    # Documented consumer flow: list -> startSession -> answer.
    types_seen = [m["type"] for m in script.received]
    assert types_seen[0] == "list"
    assert types_seen[1] == "startSession"
    answer = next(m for m in script.received if m["type"] == "peer" and "sdp" in m)
    assert answer["sdp"]["type"] == "answer"
    assert answer["sessionId"] == "sess-1"

    # Both local candidates trickled individually (webrtcbin requirement).
    trickled = [m for m in script.received if m["type"] == "peer" and "ice" in m]
    assert len(trickled) == 2
    assert {t["ice"]["sdpMLineIndex"] for t in trickled} == {0}
    assert all(t["ice"]["candidate"].startswith("candidate:") for t in trickled)

    # The server's remote candidate was fed to the peer connection.
    await _wait_for(lambda: len(pc.candidates) == 1)
    assert pc.candidates[0].sdpMLineIndex == 0

    await client.release()
    await _wait_for(lambda: pc.closed)
    await _wait_for(
        lambda: {"type": "endSession", "sessionId": "sess-1"} in script.received
    )


async def test_jpeg_encoded_once_per_frame(signalling_server, http_session) -> None:
    _, port = signalling_server
    pc = FakePeerConnection()
    client = _make_client(port, http_session, pc)
    await client.acquire()
    await pc.track.queue.put(make_frame())
    first = await client.async_get_image(timeout=5)
    second = await client.async_get_image(timeout=5)
    assert first is second  # cached by frame identity, not re-encoded
    await client.release()
    await client.async_shutdown()


async def test_shutdown_stops_session(signalling_server, http_session) -> None:
    _, port = signalling_server
    pc = FakePeerConnection()
    client = _make_client(port, http_session, pc)
    await client.acquire()
    await pc.track.queue.put(make_frame())
    await client.async_get_image(timeout=5)
    await client.async_shutdown()
    await _wait_for(lambda: pc.closed)


async def test_no_producer_fails_fast_and_cools_down(
    signalling_server, http_session
) -> None:
    from custom_components.reachy_mini.stream import StreamUnavailableError

    script, port = signalling_server
    script.behavior = "no_producer"
    pc = FakePeerConnection()
    client = _make_client(port, http_session, pc, cooldown=30.0)

    await client.acquire()
    with pytest.raises(StreamUnavailableError):
        await client.async_get_image(timeout=5)
    await client.release()

    # Within the cooldown a new acquire must NOT reconnect...
    await client.acquire()
    with pytest.raises(StreamUnavailableError):
        await client.async_get_image(timeout=1)
    await client.release()
    # ...which shows as exactly one `list` request server-side.
    assert [m["type"] for m in script.received].count("list") == 1
    await client.async_shutdown()


async def test_producer_end_session_surfaces_as_unavailable(
    signalling_server, http_session
) -> None:
    from custom_components.reachy_mini.stream import StreamUnavailableError

    script, port = signalling_server
    script.behavior = "end_session"
    pc = FakePeerConnection()
    client = _make_client(port, http_session, pc)

    await client.acquire()
    with pytest.raises(StreamUnavailableError):
        await client.async_get_image(timeout=5)
    await client.release()
    await client.async_shutdown()


async def test_unreachable_robot_raises(socket_enabled: None, http_session) -> None:
    """No signalling server at all (robot off) -> prompt failure."""
    from custom_components.reachy_mini.stream import (
        ReachyMiniStreamClient,
        StreamUnavailableError,
    )

    client = ReachyMiniStreamClient(
        "127.0.0.1", session=http_session, port=1,  # nothing listens here
        pc_factory=FakePeerConnection, idle_timeout=0.05,
    )
    await client.acquire()
    with pytest.raises(StreamUnavailableError):
        await client.async_get_image(timeout=5)
    await client.release()
    await client.async_shutdown()


async def test_release_keeps_session_for_idle_grace(
    signalling_server, http_session
) -> None:
    _, port = signalling_server
    pc = FakePeerConnection()
    client = _make_client(port, http_session, pc, idle_timeout=0.2)
    await client.acquire()
    await pc.track.queue.put(make_frame())
    await client.async_get_image(timeout=5)

    await client.release()
    await asyncio.sleep(0.05)
    assert not pc.closed  # still inside the grace period
    await _wait_for(lambda: pc.closed)  # idle timer fired


async def test_reacquire_within_grace_cancels_teardown(
    signalling_server, http_session
) -> None:
    _, port = signalling_server
    pc = FakePeerConnection()
    client = _make_client(port, http_session, pc, idle_timeout=0.2)
    await client.acquire()
    await pc.track.queue.put(make_frame())
    await client.async_get_image(timeout=5)

    await client.release()
    await client.acquire()  # back within the grace period
    await asyncio.sleep(0.4)
    assert not pc.closed  # teardown was cancelled
    jpeg = await client.async_get_image(timeout=5)
    assert jpeg.startswith(b"\xff\xd8")
    await client.async_shutdown()
    await _wait_for(lambda: pc.closed)


async def test_stale_idle_stop_does_not_kill_regranted_session(
    signalling_server, http_session
) -> None:
    """A fired-but-not-yet-run idle teardown must not survive a re-arm."""
    _, port = signalling_server
    pc = FakePeerConnection()
    client = _make_client(port, http_session, pc, idle_timeout=0.2)
    await client.acquire()
    await pc.track.queue.put(make_frame())
    await client.async_get_image(timeout=5)

    await client.release()  # arms a timer (generation bumped)
    await client.acquire()  # re-earn the session (generation bumped again)
    await client._idle_stop(0)  # stale generation: must be a no-op
    assert not pc.closed
    jpeg = await client.async_get_image(timeout=5)
    assert jpeg.startswith(b"\xff\xd8")

    # The dangerous interleaving: a fresh timer armed at consumers == 0
    # while a stale fired-but-not-yet-run task finally gets the lock.
    # Without the generation check it would stop the session right away,
    # cutting the fresh grace period short.
    await client.release()  # arms a fresh timer (generation bumped)
    await client._idle_stop(client._idle_generation - 1)  # stale task runs
    assert not pc.closed  # fresh grace period intact
    await client.async_shutdown()
    await _wait_for(lambda: pc.closed)


async def test_cancel_during_connect_is_clean_stop_no_cooldown(
    signalling_server, http_session
) -> None:
    """A stop racing ws_connect must not arm the retry cooldown.

    Only the CancelledError raised around ``_signalling_loop`` used to
    set ``clean_stop``. A stop/shutdown that cancels the session task
    while it is still awaiting ``ws_connect`` (before the signalling
    loop even starts) fell through to the outer handler with
    ``clean_stop`` still False, arming a spurious cooldown for what was
    a deliberate teardown.
    """
    script, port = signalling_server
    script.behavior = "stall"
    stalled_pc = FakePeerConnection()
    client = _make_client(port, http_session, stalled_pc, cooldown=30.0)

    await client.acquire()
    await asyncio.sleep(0.05)  # let the task block inside ws_connect
    await client.async_shutdown()

    assert client._cooldown_until == 0.0  # no cooldown armed by a clean stop

    # A fresh acquire must not fail-fast on a bogus cooldown, and must
    # actually negotiate a brand new session.
    script.behavior = "happy"
    happy_pc = FakePeerConnection()
    client._pc_factory = lambda: happy_pc
    await client.acquire()
    await happy_pc.track.queue.put(make_frame())
    jpeg = await client.async_get_image(timeout=5)
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
    await client.async_shutdown()
    await _wait_for(lambda: happy_pc.closed)

"""Shared WebRTC consumer session for the Reachy Mini camera.

The robot streams camera + audio through GStreamer ``webrtcsink`` with
its built-in gst-webrtc-signalling server on ``ws://<host>:8443``. That
producer only supports the producer-offers flow (consumer-initiated
offers are answered with an immediate ``endSession``), and HA's frontend
can only be a WebRTC *offerer* — so this module terminates the robot's
WebRTC media on the HA host with aiortc and hands decoded frames to the
camera entity.

Interop notes (verified against a live robot, daemon 1.8.4 / gst 1.28.3):

- The robot's Linux GStreamer build has an RSA DTLS certificate while
  aiortc's default cipher list is ECDSA-only; RSA ECDHE suites must be
  offered or the handshake dies with a fatal alert 40 — see
  :class:`InteropCertificate`.
- gst ``webrtcbin`` ignores ``a=candidate`` lines embedded in the answer
  SDP; local ICE candidates must be trickled as individual ``ice``
  signalling messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from fractions import Fraction
from typing import Any

import aiohttp
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from aiortc.rtcdtlstransport import RTCCertificate
from aiortc.sdp import candidate_from_sdp
from OpenSSL import SSL

from .const import (
    CAMERA_IDLE_TIMEOUT,
    CAMERA_RETRY_COOLDOWN,
    DTLS_CIPHER_LIST,
    PRODUCER_NAME,
    SIGNALLING_PORT,
)

_LOGGER = logging.getLogger(__name__)


class InteropCertificate(RTCCertificate):
    """RTCCertificate whose DTLS context offers RSA ECDHE suites too."""

    def _create_ssl_context(self, srtp_profiles: list) -> SSL.Context:
        ctx = super()._create_ssl_context(srtp_profiles)
        ctx.set_cipher_list(DTLS_CIPHER_LIST)
        return ctx

    @classmethod
    def generate(cls) -> InteropCertificate:
        """Self-signed certificate, like aiortc's default one."""
        base = RTCCertificate.generateCertificate()
        return cls(key=base._key, cert=base._cert)


class StreamUnavailableError(Exception):
    """The robot's camera stream cannot be reached right now."""


def _default_pc_factory() -> RTCPeerConnection:
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    # aiortc has no public way to supply a DTLS certificate — its
    # RTCConfiguration lacks the spec's `certificates` field (checked
    # at 1.14) — so replace the auto-generated one on the private
    # attribute. test_default_pc_factory_installs_interop_certificate
    # pins this against aiortc upgrades.
    pc._RTCPeerConnection__certificates = [InteropCertificate.generate()]
    return pc


class _JpegEncoder:
    """One bare codec context; avoids per-frame container reference cycles.

    Access is serialized by the client's single-flight task. No container,
    stream or muxer is needed: each MJPEG packet is a complete JPEG.
    """

    def __init__(self) -> None:
        self._codec = None
        self._size = None

    def encode(self, frame: Any) -> bytes:
        import av

        size = (frame.width, frame.height)
        if self._codec is None or self._size != size:
            self.close()
            codec = av.CodecContext.create("mjpeg", "w")
            codec.width, codec.height = size
            codec.pix_fmt = "yuvj420p"
            codec.time_base = Fraction(1, 90000)
            codec.thread_count = 1
            # A reusable MJPEG codec must not tighten compression over time
            # to satisfy the bare context's default 200 kbps rate target.
            codec.bit_rate = 0
            codec.qscale = True
            codec.global_quality = 8  # PyAV takes QP units, not FF_QP2LAMBDA.
            codec.open()
            self._codec = codec
            self._size = size
        packets = self._codec.encode(frame.reformat(format="yuvj420p"))
        if len(packets) != 1:
            raise StreamUnavailableError("JPEG encoder did not return one image")
        return bytes(packets[0])

    def close(self) -> None:
        # Bare CodecContext owns its native resources without a container cycle.
        # Only drop it after the worker has finished; never flush per frame.
        self._codec = None
        self._size = None


def _encode_jpeg(frame: Any) -> bytes:
    """One-shot helper for callers/tests; live viewing reuses _JpegEncoder."""
    encoder = _JpegEncoder()
    try:
        return encoder.encode(frame)
    finally:
        encoder.close()


class ReachyMiniStreamClient:
    """One shared consumer session, reference-counted by HA consumers.

    All HA-side consumers (still images, MJPEG viewers) share a single
    robot session: the robot encodes once for HA no matter how many
    dashboards are watching, and HA's session coexists with the robot's
    other consumers (apps, mobile/desktop remote sessions).
    """

    def __init__(
        self,
        host: str,
        *,
        session: aiohttp.ClientSession,
        port: int = SIGNALLING_PORT,
        pc_factory: Callable[[], Any] | None = None,
        idle_timeout: float = CAMERA_IDLE_TIMEOUT,
        cooldown: float = CAMERA_RETRY_COOLDOWN,
    ) -> None:
        """Bind to one robot; ``pc_factory`` is injectable for tests."""
        self._host = host
        self._port = port
        self._session = session
        self._pc_factory = pc_factory or _default_pc_factory
        self._idle_timeout = idle_timeout
        self._cooldown = cooldown
        self._lock = asyncio.Lock()
        self._consumers = 0
        self._task: asyncio.Task | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._idle_task: asyncio.Task | None = None
        # Cancelling the handle alone is not enough: a timer that has
        # already fired has queued an _idle_stop task that can outlive
        # a re-arm and stop the fresh session. Every cancel/re-arm bumps
        # the generation so a stale task sees a mismatch and bails.
        self._idle_generation = 0
        self._video_task: asyncio.Task | None = None
        self._session_id: str | None = None
        self._cooldown_until = 0.0
        self._frame: Any | None = None
        self._frame_event = asyncio.Event()
        self._jpeg_cache: tuple[Any, bytes] | None = None
        self._jpeg_encoder = _JpegEncoder()
        self._jpeg_task: asyncio.Task | None = None
        self._audio_tasks: set[asyncio.Task] = set()

    @property
    def signalling_url(self) -> str:
        """gst-webrtc-signalling endpoint on the robot."""
        return f"ws://{self._host}:{self._port}"

    async def acquire(self) -> None:
        """Register a consumer; starts the robot session if needed."""
        async with self._lock:
            self._consumers += 1
            if self._idle_handle is not None:
                self._idle_handle.cancel()
                self._idle_handle = None
            self._idle_generation += 1
            if self._task is not None and not self._task.done():
                return
            loop = asyncio.get_running_loop()
            if loop.time() < self._cooldown_until:
                # Recent robot-side failure: let waiters fail fast
                # instead of hammering the robot with reconnects.
                self._frame_event.set()
                return
            self._frame = None
            self._jpeg_cache = None
            self._frame_event = asyncio.Event()
            self._task = loop.create_task(self._run_session())

    async def release(self) -> None:
        """Deregister a consumer; arms idle teardown at zero.

        The grace period keeps snapshot bursts (thumbnail + notification
        + automation) on one robot session instead of three.
        """
        async with self._lock:
            self._consumers = max(0, self._consumers - 1)
            if self._consumers > 0 or self._task is None:
                return
            if self._idle_handle is not None:
                self._idle_handle.cancel()
                self._idle_handle = None
            self._idle_generation += 1
            gen = self._idle_generation
            loop = asyncio.get_running_loop()
            self._idle_handle = loop.call_later(
                self._idle_timeout,
                lambda: self._schedule_idle_stop(gen),
            )

    def _schedule_idle_stop(self, gen: int) -> None:
        """Create the idle-teardown task and keep a reference to it."""
        task = asyncio.get_running_loop().create_task(self._idle_stop(gen))
        self._idle_task = task
        task.add_done_callback(self._log_idle_task_result)

    @staticmethod
    def _log_idle_task_result(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.debug("Idle stop task failed: %s", exc, exc_info=exc)

    async def _idle_stop(self, gen: int) -> None:
        async with self._lock:
            if gen != self._idle_generation:
                return  # superseded by a later release()/acquire()
            self._idle_handle = None
            if self._consumers == 0:
                await self._stop_session()

    async def async_shutdown(self) -> None:
        """Tear everything down immediately (config entry unload)."""
        async with self._lock:
            self._consumers = 0
            if self._idle_handle is not None:
                self._idle_handle.cancel()
                self._idle_handle = None
            self._idle_generation += 1
            await self._stop_session()

    async def async_get_image(self, timeout: float = 10.0) -> bytes:
        """Latest frame as JPEG. The caller must hold an acquire()."""
        try:
            await asyncio.wait_for(self._frame_event.wait(), timeout)
        except TimeoutError:
            raise StreamUnavailableError(
                "timed out waiting for a camera frame"
            ) from None
        frame = self._frame
        if frame is None:
            raise StreamUnavailableError("camera stream is not available")
        cache = self._jpeg_cache
        if cache is not None and cache[0] is frame:
            return cache[1]
        # No await between checking and publishing the shared task. A cancelled
        # viewer must not cancel a worker still used by other viewers.
        task = self._jpeg_task
        if task is None or task.done():
            task = asyncio.create_task(self._encode_frame(frame))
            task.add_done_callback(self._log_idle_task_result)
            self._jpeg_task = task
        return await asyncio.shield(task)

    async def _encode_frame(self, frame: Any) -> bytes:
        try:
            jpeg = await asyncio.get_running_loop().run_in_executor(
                None, self._jpeg_encoder.encode, frame
            )
        except Exception as err:
            raise StreamUnavailableError("JPEG encoding failed") from err
        self._jpeg_cache = (frame, jpeg)
        return jpeg

    async def _clear_media(self) -> None:
        # Reject new image work before waiting for the existing executor job.
        self._frame = None
        self._frame_event.set()
        task = self._jpeg_task
        if task is not None:
            with contextlib.suppress(Exception):
                await asyncio.shield(task)
        self._jpeg_task = None
        self._jpeg_cache = None
        self._jpeg_encoder.close()

    async def _stop_session(self) -> None:
        # Await the cancelled task: stop and acquire both run under
        # self._lock, so the old session's finally block (which clears
        # frame state and wakes waiters) must fully finish before a new
        # session can start — otherwise it would clobber the new
        # session's state.
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._clear_media()

    async def _run_session(self) -> None:
        loop = asyncio.get_running_loop()
        pc = self._pc_factory()
        self._session_id = None
        clean_stop = False
        try:
            async with self._session.ws_connect(
                self.signalling_url, heartbeat=20
            ) as ws:
                try:
                    await self._signalling_loop(ws, pc)
                finally:
                    # Polite teardown mirrors the SDK clients. After a
                    # cancellation this await is allowed to run (and to
                    # fail — the ws may already be gone).
                    if self._session_id is not None and not ws.closed:
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(
                                ws.send_json(
                                    {
                                        "type": "endSession",
                                        "sessionId": self._session_id,
                                    }
                                ),
                                1,
                            )
        except asyncio.CancelledError:
            # Any cancellation here (including one that lands while
            # still awaiting ws_connect, before the signalling loop
            # ever starts) is a deliberate teardown (_stop_session /
            # async_shutdown), not a robot-side failure — it must not
            # arm the retry cooldown.
            clean_stop = True
            raise
        except (aiohttp.ClientError, OSError, ValueError) as err:
            _LOGGER.warning("Reachy Mini camera stream error: %s", err)
        finally:
            if not clean_stop:
                self._cooldown_until = loop.time() + self._cooldown
            # A stop can arrive while a naturally-ended session is already
            # cleaning up. Cancelling pc.close() can leave aiortc's close
            # future unfinished, so finish cleanup before propagating it.
            cleanup = asyncio.create_task(self._finish_session(pc))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.shield(cleanup)
                raise

    async def _finish_session(self, pc: Any) -> None:
        video_task = self._video_task
        self._video_task = None
        if video_task is not None:
            video_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await video_task
        audio_tasks = tuple(self._audio_tasks)
        self._audio_tasks.clear()
        for task in audio_tasks:
            task.cancel()
        await asyncio.gather(*audio_tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await pc.close()
        await self._clear_media()
        self._frame = None
        self._jpeg_cache = None
        # Wake any waiters so they fail fast instead of timing out.
        self._frame_event.set()

    async def _signalling_loop(self, ws: Any, pc: Any) -> None:
        pc.on("track", self._on_track)
        async for raw in ws:
            if raw.type != aiohttp.WSMsgType.TEXT:
                return
            msg = json.loads(raw.data)
            mtype = msg.get("type")
            if mtype == "welcome":
                await ws.send_json({"type": "list"})
            elif mtype == "list":
                producer_id = next(
                    (
                        p["id"]
                        for p in msg.get("producers", [])
                        if p.get("meta", {}).get("name") == PRODUCER_NAME
                    ),
                    None,
                )
                if producer_id is None:
                    _LOGGER.warning(
                        "Reachy Mini camera producer %r not advertised "
                        "(robot asleep or media held exclusively?)",
                        PRODUCER_NAME,
                    )
                    return
                await ws.send_json(
                    {"type": "startSession", "peerId": producer_id}
                )
            elif mtype == "sessionStarted":
                self._session_id = msg["sessionId"]
            elif mtype == "peer" and "sdp" in msg:
                await pc.setRemoteDescription(
                    RTCSessionDescription(msg["sdp"]["sdp"], msg["sdp"]["type"])
                )
                await pc.setLocalDescription(await pc.createAnswer())
                sdp = pc.localDescription.sdp
                await ws.send_json(
                    {
                        "type": "peer",
                        "sessionId": self._session_id,
                        "sdp": {"type": "answer", "sdp": sdp},
                    }
                )
                # webrtcbin ignores in-SDP candidates: trickle each one.
                mline = -1
                for line in sdp.splitlines():
                    if line.startswith("m="):
                        mline += 1
                    elif line.startswith("a=candidate:"):
                        await ws.send_json(
                            {
                                "type": "peer",
                                "sessionId": self._session_id,
                                "ice": {
                                    "candidate": line[2:],
                                    "sdpMLineIndex": mline,
                                },
                            }
                        )
            elif mtype == "peer" and "ice" in msg:
                ice = msg["ice"] or {}
                cand = ice.get("candidate")
                if cand:
                    candidate = candidate_from_sdp(
                        cand.replace("candidate:", "", 1)
                    )
                    candidate.sdpMLineIndex = ice.get("sdpMLineIndex", 0)
                    await pc.addIceCandidate(candidate)
            elif mtype == "endSession":
                _LOGGER.debug("Producer ended the camera session")
                return

    def _on_track(self, track: Any) -> None:
        if track.kind == "video" and self._video_task is None:
            self._video_task = asyncio.get_running_loop().create_task(
                self._consume_video(track)
            )

        elif track.kind == "audio":
            task = asyncio.create_task(self._discard_audio(track))
            self._audio_tasks.add(task)
            task.add_done_callback(self._audio_tasks.discard)
            task.add_done_callback(self._log_idle_task_result)

    async def _discard_audio(self, track: Any) -> None:
        """Drain unused audio so aiortc's decoded-frame queue stays bounded."""
        try:
            while True:
                await track.recv()
        except MediaStreamError:
            pass

    async def _consume_video(self, track: Any) -> None:
        try:
            while True:
                self._frame = await track.recv()
                self._frame_event.set()
        except MediaStreamError:
            _LOGGER.debug("Reachy Mini video track ended")

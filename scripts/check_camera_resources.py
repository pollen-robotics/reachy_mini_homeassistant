"""Cloud-only resource regression checks; no forced garbage collection."""
import asyncio
import gc
import faulthandler
import io
import json
import threading
import time
import sys
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import av
from aiortc import VideoStreamTrack, AudioStreamTrack, RTCSessionDescription
import importlib
import types

# Load the actual stream module without importing Home Assistant's entry point.
# Codec and WebRTC dependencies are real.
package = types.ModuleType("reachy_media_check")
package.__path__ = [str(Path("custom_components/reachy_mini").resolve())]
sys.modules[package.__name__] = package
media = importlib.import_module("reachy_media_check.stream")

def usage():
    fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    return int(fields["VmRSS"].split()[0]) / 1024, int(fields["Threads"])

def frame(w=640, h=480):
    f = av.VideoFrame(w, h, "yuv420p")
    for p in f.planes:
        p.update(bytes(p.buffer_size))
    return f

def check_jpeg(data, size):
    with av.open(io.BytesIO(data)) as source:
        result = next(source.decode(video=0))
        assert (result.width, result.height) == size

def encoder_stress():
    # Disable cyclic GC only in this standalone test process. Passing requires
    # prompt native-resource release, rather than waiting for periodic GC.
    gc.disable()
    encoder = media._JpegEncoder()
    f = frame()
    for _ in range(30):
        encoder.encode(f)
    baseline = usage()
    for i in range(3000):
        data = encoder.encode(f)
        if i % 500 == 0:
            print("JPEG", i, usage(), flush=True)
    end = usage()
    assert end[0] - baseline[0] < 48, (baseline, end)
    assert end[1] <= baseline[1] + 2, (baseline, end)
    check_jpeg(data, (640, 480))
    for i in range(100):
        size = (320, 240) if i % 2 else (640, 480)
        data = encoder.encode(frame(*size))
    check_jpeg(data, size)
    encoder.close()
    # Also guard the public one-shot helper against the original 30-frame leak.
    before = usage()
    for _ in range(300):
        media._encode_jpeg(f)
    after = usage()
    assert after[0] - before[0] < 48, (before, after)
    assert after[1] <= before[1] + 2, (before, after)
    gc.enable()
    print("PASS 3000 JPEG frames, resize, 300 one-shot calls without cyclic GC", usage(), flush=True)

async def concurrency():
    client = media.ReachyMiniStreamClient("unused", session=None)
    client._frame = frame()
    client._frame_event.set()
    entered, finish = threading.Event(), threading.Event()
    calls = 0
    original = client._jpeg_encoder.encode
    def blocked(f):
        nonlocal calls
        calls += 1
        entered.set()
        assert finish.wait(5)
        return original(f)
    client._jpeg_encoder.encode = blocked
    requests = [asyncio.create_task(client.async_get_image()) for _ in range(20)]
    while not entered.is_set():
        await asyncio.sleep(.001)
    requests[0].cancel()
    await asyncio.sleep(.02)
    assert calls == 1
    shutdown = asyncio.create_task(client.async_shutdown())
    await asyncio.sleep(.02)
    assert not shutdown.done(), "shutdown must await in-flight native encoding"
    finish.set()
    results = await asyncio.gather(*requests, return_exceptions=True)
    await shutdown
    assert isinstance(results[0], asyncio.CancelledError)
    assert all(isinstance(x, bytes) for x in results[1:])
    assert len({id(x) for x in results[1:]}) == 1
    assert client._jpeg_encoder._codec is None
    assert client._jpeg_cache is None and client._jpeg_task is None
    print("PASS 20 simultaneous requests, cancellation and shutdown", flush=True)

async def cancelled_cleanup():
    started, finish = asyncio.Event(), asyncio.Event()
    class PC:
        async def close(self):
            started.set()
            await finish.wait()
    class Session:
        def ws_connect(self, *args, **kwargs):
            raise OSError("expected test disconnect")
    client = media.ReachyMiniStreamClient("unused", session=Session(), pc_factory=PC)
    await client.acquire()
    await asyncio.wait_for(started.wait(), 2)
    shutdown = asyncio.create_task(client.async_shutdown())
    await asyncio.sleep(.02)
    assert not shutdown.done()
    finish.set()
    await asyncio.wait_for(shutdown, 2)
    print("PASS stop during natural session cleanup", flush=True)

async def streaming(producer=False):
    peers = []
    close_tasks = []
    tracks = []
    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        pc = media._default_pc_factory()
        peers.append(pc)
        video, audio = VideoStreamTrack(), AudioStreamTrack()
        tracks.extend((video, audio))
        pc.addTrack(video)
        pc.addTrack(audio)
        try:
            await ws.send_json({"type": "welcome"})
            async for raw in ws:
                msg = json.loads(raw.data)
                if msg["type"] == "list":
                    await ws.send_json({"type": "list", "producers": [{"id": "p", "meta": {"name": "reachymini"}}]})
                elif msg["type"] == "startSession":
                    await ws.send_json({"type": "sessionStarted", "sessionId": "s"})
                    await pc.setLocalDescription(await pc.createOffer())
                    await ws.send_json({"type": "peer", "sdp": {"type": "offer", "sdp": pc.localDescription.sdp}})
                elif msg["type"] == "peer" and "sdp" in msg:
                    await pc.setRemoteDescription(RTCSessionDescription(**msg["sdp"]))
                elif msg["type"] == "endSession":
                    break
        finally:
            close_task = asyncio.create_task(pc.close())
            close_tasks.append(close_task)
            await asyncio.shield(close_task)
            video.stop()
            audio.stop()
            await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    if producer:
        await server.start_server()
        print(server.port, flush=True)
        await asyncio.Event().wait()
        return
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", __file__, "--producer",
        stdout=asyncio.subprocess.PIPE,
    )
    port = int(await asyncio.wait_for(process.stdout.readline(), 10))
    receiver_pcs = []
    connection_count = 0
    def factory():
        nonlocal connection_count
        connection_count += 1
        pc = media._default_pc_factory()
        receiver_pcs.append(pc)
        return pc
    try:
        async with aiohttp.ClientSession() as session:
            client = media.ReachyMiniStreamClient("127.0.0.1", port=port, session=session, pc_factory=factory, idle_timeout=.1)
            samples = []
            closed_samples = []
            for cycle in range(20):
                await client.acquire()
                await client.acquire()
                await client.acquire()
                duration = 120 if cycle == 0 else 2
                deadline = time.monotonic() + duration
                count = 0
                while time.monotonic() < deadline:
                    images = await asyncio.gather(*(client.async_get_image() for _ in range(3)))
                    assert all(x.startswith(b"\xff\xd8") for x in images)
                    pc = receiver_pcs[-1]
                    audio = [r.track for r in pc.getReceivers() if r.track and r.track.kind == "audio"]
                    assert len(audio) == 1
                    assert audio[0]._queue.qsize() < 10
                    count += 1
                    if count % 100 == 0:
                        samples.append(usage())
                        print("LIVE", cycle, count, samples[-1], flush=True)
                    await asyncio.sleep(.1)
                await client.release()
                await client.release()
                assert receiver_pcs[-1].connectionState != "closed"
                await client.release()
                for _ in range(500):
                    if receiver_pcs[-1].connectionState == "closed" and client._jpeg_encoder._codec is None:
                        break
                    await asyncio.sleep(.01)
                else:
                    raise AssertionError("idle cleanup did not finish")
                assert not client._audio_tasks
                assert client._video_task is None
                assert client._jpeg_task is None
                assert client._frame is None
                assert not any(t.name in ("audio-decoder", "video-decoder") for t in threading.enumerate())
                del pc, audio
                receiver_pcs.clear()
                closed_samples.append(usage())
                print("CLOSED", cycle, closed_samples[-1], flush=True)
            assert connection_count == 20
            # Receiver-only process: sender codec caches cannot mask regressions.
            assert max(x[1] for x in closed_samples[3:]) - min(x[1] for x in closed_samples[3:]) <= 2, closed_samples
            assert max(x[0] for x in closed_samples[3:]) - closed_samples[3][0] < 32, closed_samples
            # Ignore startup allocator warmup; allow bounded native allocator caches.
            assert max(x[0] for x in samples[2:]) - samples[2][0] < 96, samples
            assert max(x[1] for x in samples[2:]) - min(x[1] for x in samples[2:]) <= 4, samples
            await client.async_shutdown()
    finally:
        await asyncio.wait_for(asyncio.gather(*(p.close() for p in receiver_pcs)), 10)
        # The separate producer represents an always-running robot. Terminating
        # this test fixture does not contribute to receiver RSS/thread samples.
        process.terminate()
        await asyncio.wait_for(process.wait(), 10)
    print("PASS real WebRTC audio/video, 3 viewers, 120-second view, 20 open/close cycles, receiver measured separately", flush=True)

def quality_regression():
    import math
    import random
    rng = random.Random(42)
    w, h = 640, 480
    patterns = {
        "fine_detail": bytes(rng.randrange(256) for _ in range(w*h)),
        "checker": bytes(220 if (x//3+y//3)%2 else 30 for y in range(h) for x in range(w)),
        "gradient": bytes(x*255//(w-1) for y in range(h) for x in range(w)),
    }
    def source(raw, i):
        f = av.VideoFrame(w, h, "gray")
        p = f.planes[0]
        padded = b"".join(raw[y*w:(y+1)*w] + bytes(p.line_size-w) for y in range(h))
        p.update(padded)
        from fractions import Fraction
        f.pts = i*3000
        f.time_base = Fraction(1, 90000)
        return f
    def legacy(f):
        buffer = io.BytesIO()
        with av.open(buffer, mode="w", format="mjpeg") as c:
            stream = c.add_stream("mjpeg", rate=1)
            stream.width, stream.height = w, h
            stream.pix_fmt = "yuvj420p"
            for p in stream.encode(f.reformat(format="yuvj420p")):
                c.mux(p)
            for p in stream.encode():
                c.mux(p)
        return buffer.getvalue()
    def psnr(data, raw):
        with av.open(io.BytesIO(data)) as c:
            f = next(c.decode(video=0)).reformat(format="gray")
            p = f.planes[0]
            padded = bytes(p)
            decoded = b"".join(padded[y*p.line_size:y*p.line_size+w] for y in range(h))
        mse = sum((a-b)**2 for a,b in zip(decoded,raw))/(w*h)
        return 100.0 if not mse else 10*math.log10(255**2/mse)
    for name, raw in patterns.items():
        old = legacy(source(raw, 0))
        encoder = media._JpegEncoder()
        first = encoder.encode(source(raw, 0))
        for i in range(1, 60):
            last = encoder.encode(source(raw, i))
        encoder.close()
        assert first == last, "quality must not deteriorate on repeated identical input"
        before, after = psnr(old, raw), psnr(last, raw)
        assert after >= before - .1, (name, before, after)
        print("QUALITY", name, "old/new PSNR", before, after, "bytes", len(old), len(last), flush=True)
    print("PASS fixed JPEG quality across 60 frames and three detail patterns", flush=True)

async def main():
    quality_regression()
    encoder_stress()
    await concurrency()
    await cancelled_cleanup()
    await streaming()

if __name__ == "__main__":
    if "--producer" in sys.argv:
        asyncio.run(streaming(producer=True))
        sys.exit(0)
    faulthandler.dump_traceback_later(240, exit=True)
    try:
        asyncio.run(main())
    finally:
        faulthandler.cancel_dump_traceback_later()

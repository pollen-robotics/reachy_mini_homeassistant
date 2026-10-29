"""Exercise real codecs/ICE/DTLS/SRTP and the integration's JPEG encoder.

Runs only on a GitHub-hosted runner. It cannot establish that a physical
robot's GStreamer signalling or Home Assistant discovery works.
"""
import asyncio
import importlib
import importlib.metadata
import io
from pathlib import Path
import sys
import types

import av
from aiortc import AudioStreamTrack, RTCRtpSender, VideoStreamTrack
from aiortc.rtcdtlstransport import SRTP_PROFILES
from OpenSSL import SSL

# Import the actual media code without importing HA's integration entry point.
# No fake media dependencies: aiortc, av, OpenSSL and aiohttp are real.
package = types.ModuleType("reachy_media_check")
package.__path__ = [str(Path("custom_components/reachy_mini").resolve())]
sys.modules[package.__name__] = package
media = importlib.import_module("reachy_media_check.stream")

async def check_codec(mime):
    sender, receiver = media._default_pc_factory(), media._default_pc_factory()
    video, audio = VideoStreamTrack(), AudioStreamTrack()
    received = {}
    tasks = []
    async def consume(track):
        frames = []
        for _ in range(5):
            frames.append(await track.recv())
        received[track.kind] = frames
    @receiver.on("track")
    def on_track(track):
        tasks.append(asyncio.create_task(consume(track)))
    try:
        sender.addTrack(video)
        sender.addTrack(audio)
        codec = [c for c in RTCRtpSender.getCapabilities("video").codecs if c.mimeType.lower() == mime.lower()]
        assert codec, mime
        sender.getTransceivers()[0].setCodecPreferences(codec)
        await sender.setLocalDescription(await sender.createOffer())
        await receiver.setRemoteDescription(sender.localDescription)
        await receiver.setLocalDescription(await receiver.createAnswer())
        await sender.setRemoteDescription(receiver.localDescription)
        assert len(tasks) == 2
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)
        assert len(received["video"]) == len(received["audio"]) == 5
        frame = received["video"][-1]
        assert frame.width == 640 and frame.height == 480
        jpeg = media._encode_jpeg(frame)
        assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
        with av.open(io.BytesIO(jpeg)) as image:
            decoded = next(image.decode(video=0))
            assert (decoded.width, decoded.height) == (640, 480)
        print(f"PASS {mime}: 5 real video frames, 5 audio frames, JPEG round trip")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        video.stop()
        audio.stop()
        await asyncio.gather(sender.close(), receiver.close())

async def main():
    assert importlib.metadata.version("av") == "19.0.0"
    assert importlib.metadata.version("aiortc") == "1.14.0+av19.1"
    cert = media.InteropCertificate.generate()
    ciphers = SSL.Connection(cert._create_ssl_context(SRTP_PROFILES)).get_cipher_list()
    assert "ECDHE-RSA-AES128-GCM-SHA256" in ciphers
    assert "ECDHE-ECDSA-AES128-GCM-SHA256" in ciphers
    await check_codec("video/H264")
    await check_codec("video/VP8")
    print("Media checks passed; physical robot and HA checks remain required.")

if __name__ == "__main__":
    asyncio.run(main())

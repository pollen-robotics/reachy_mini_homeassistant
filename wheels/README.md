# Compatibility wheels

`aiortc-1.14.0+av19.1-py3-none-any.whl` allows the AV19 version pinned by
Home Assistant 2026.10.1. It changes the distribution version and the
`av` upper bound to `<20`, then regenerates RECORD. Runtime code and
licenses are unchanged.

Run `python scripts/build_av19_wheel.py` to reproduce the wheel. The source
is the retained `aiortc-1.14.0+av17-py3-none-any.whl`, a metadata-only
repackage of upstream aiortc 1.14.0 which relaxed `av<17` to `av<19`.
The build script checks its SHA256 before making changes.

Output SHA256:
`bc7b96ef1acca84d73bf2ea43fcc2d63575136e25c2cbd8136ddf3afb6033403`

The AV19 workflow checks reproducibility, dependency resolution, integration
imports, H.264/VP8 video, audio and JPEG encoding with HA 2026.10.1 dependencies.
It does not validate every aiortc feature or every older AV version.

The wheel stays in the repository because HACS treats release tags as
integration versions. The manifest URL includes a SHA256 integrity pin.
Replace this wheel with a normal PyPI requirement when an upstream aiortc
release supports HA's pinned AV version.

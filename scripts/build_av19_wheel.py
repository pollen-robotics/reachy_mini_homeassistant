"""Build a deterministic compatibility wheel in GitHub Actions, never in HA."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile

SOURCE = Path("wheels/aiortc-1.14.0+av17-py3-none-any.whl")
EXPECTED = "f2e9a64ab8bf0b13cad1ff0cf97e137d5f18c429a1e4ba8ac33865a3c2507495"
VERSION = "1.14.0+av19.1"
DEST = Path("dist") / f"aiortc-{VERSION}-py3-none-any.whl"

def main():
    data = SOURCE.read_bytes()
    assert hashlib.sha256(data).hexdigest() == EXPECTED, "Unexpected source wheel"
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        metadata_path = next(n for n in source.namelist() if n.endswith(".dist-info/METADATA"))
        old_info = metadata_path.rsplit("/", 1)[0]
        new_info = f"aiortc-{VERSION}.dist-info"
        files = {}
        for name in source.namelist():
            if name.endswith("/") or name == old_info + "/RECORD":
                continue
            content = source.read(name)
            if name == metadata_path:
                lines = content.decode().splitlines()
                found_av = found_version = False
                for i, line in enumerate(lines):
                    if line.startswith("Version: "):
                        lines[i] = f"Version: {VERSION}"
                        found_version = True
                    if line.startswith("Requires-Dist: av"):
                        assert "<19" in line, line
                        lines[i] = "Requires-Dist: av>=14.0.0,<20.0.0"
                        found_av = True
                assert found_av and found_version
                content = ("\n".join(lines) + "\n").encode()
            files[name.replace(old_info + "/", new_info + "/", 1)] = content
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, content in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([name, f"sha256={digest}", len(content)])
    record_path = new_info + "/RECORD"
    writer.writerow([record_path, "", ""])
    files[record_path] = record.getvalue().encode()
    DEST.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(DEST, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            output.writestr(info, content)
    digest = hashlib.sha256(DEST.read_bytes()).hexdigest()
    Path("dist/SHA256SUMS").write_text(f"{digest}  {DEST.name}\n")
    print(f"Built {DEST}: sha256={digest}")

if __name__ == "__main__":
    main()

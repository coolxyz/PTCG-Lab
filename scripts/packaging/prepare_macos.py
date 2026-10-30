"""Explicit reproducible repairs to copied packaging inputs, never installed APPs."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import shutil
import zipfile
import stat

from scripts.packaging.desktop_inventory import payload_files, relative
from scripts.packaging.desktop_audit import macho
from scripts.packaging.common import record

PILLOW_FILE = "python/lib/python3.14/site-packages/PIL/.dylibs/libjpeg.62.4.0.dylib"
PILLOW_RPATH_SHA256 = "e090999f47726b5d284111ee608703db42a8a1604cc58cacd651b3b64aa111e6"
# Identify the known upstream CI path without publishing machine paths.


def prepare(payload, parser_wheel, *, root):
    payload = Path(payload).absolute()
    files = payload_files(payload, "macos-arm64")
    lock = json.loads(
        (Path(root) / "scripts/native/windows-wheel-lock.json").read_text(
            encoding="utf-8"
        )
    )
    parser = next(w for w in lock["wheels"] if w["name"] == "mwparserfromhell")
    parser_wheel = Path(parser_wheel)
    if (
        parser_wheel.name != parser["filename"]
        or sha256(parser_wheel.read_bytes()).hexdigest() != parser["sha256"]
        or not parser_wheel.name.endswith("-py3-none-any.whl")
    ):
        raise ValueError("Pinned pure Python parser wheel required")
    with zipfile.ZipFile(parser_wheel) as archive:
        infos = archive.infolist()
        if len({i.filename for i in infos}) != len(infos) or any(
            not relative(i.filename.rstrip("/"))
            or not i.filename.startswith(
                ("mwparserfromhell/", "mwparserfromhell-0.7.2.dist-info/")
            )
            or stat.S_ISLNK(i.external_attr >> 16)
            for i in infos
        ):
            raise ValueError("Unsafe parser wheel")
    path = payload / PILLOW_FILE
    if PILLOW_FILE not in files:
        raise ValueError("Pinned Pillow image library missing")
    metadata = (
        payload / "python/lib/python3.14/site-packages/pillow-12.3.0.dist-info/METADATA"
    )
    if not metadata.is_file():
        raise ValueError("Expected pinned Pillow version")
    slices = macho(path.read_bytes())
    if (
        len(slices) != 1
        or slices[0]["architecture"] != "arm64"
        or len(slices[0]["rpaths"]) != 1
        or sha256(slices[0]["rpaths"][0].encode()).hexdigest() != PILLOW_RPATH_SHA256
    ):
        raise ValueError("Pillow build input differs; review before applying repair")
    before = sha256(path.read_bytes()).hexdigest()
    subprocess.run(
        [
            "/usr/bin/install_name_tool",
            "-delete_rpath",
            slices[0]["rpaths"][0],
            str(path),
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if macho(path.read_bytes())[0]["rpaths"]:
        raise ValueError("Pillow repair incomplete")
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(path)], check=True, timeout=30
    )
    after = sha256(path.read_bytes()).hexdigest()
    parser_before = {
        name: digest
        for name, digest in files.items()
        if name.startswith("python/lib/python3.14/site-packages/mwparserfromhell")
    }
    site = payload / "python/lib/python3.14/site-packages"
    for folder in ("mwparserfromhell", "mwparserfromhell-0.7.2.dist-info"):
        shutil.rmtree(site / folder)
    with zipfile.ZipFile(parser_wheel) as archive:
        archive.extractall(site)
    repair = dict(
        schema=1,
        patch="pillow-12.3.0-macos-arm64-dead-build-rpath",
        file=PILLOW_FILE,
        beforeSha256=before,
        afterSha256=after,
        signature="ad_hoc",
        upstreamBytesPreserved=False,
        parser={
            "version": "0.7.2",
            "implementation": "pure-python",
            "wheelSha256": parser["sha256"],
            "previousFilesSha256": sha256(
                json.dumps(parser_before, sort_keys=True).encode()
            ).hexdigest(),
        },
    )
    record(payload / "PACKAGING-REPAIRS.json", repair)
    files = {
        p.relative_to(payload).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in sorted(payload.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    files["PACKAGING-REPAIRS.json"] = sha256(
        (payload / "PACKAGING-REPAIRS.json").read_bytes()
    ).hexdigest()
    manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = files
    (payload / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    payload_files(payload, "macos-arm64")
    return repair

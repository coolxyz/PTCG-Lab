"""Derive only macOS/Windows assets from the approved, transparent master PNG."""

from pathlib import Path
import argparse
import json
import plistlib
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "icon.png",
    "icon.icns",
    "icon.ico",
    "32x32.png",
    "128x128.png",
    "128x128@2x.png",
)


def verify(app, executable):
    """Check the packaged ICNS and every compiled Windows icon image."""
    icons = ROOT / "apps/native-shell/src-tauri/icons"
    resources = Path(app) / "Contents/Resources"
    with (Path(app) / "Contents/Info.plist").open("rb") as stream:
        name = plistlib.load(stream)["CFBundleIconFile"]
    if Path(name).name != name:
        raise ValueError("Unexpected APP icon path")
    if not name.endswith(".icns"):
        name += ".icns"
    if (resources / name).read_bytes() != (icons / "icon.icns").read_bytes():
        raise ValueError("APP does not contain the approved ICNS")
    ico = (icons / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", ico)
    if reserved or kind != 1 or count < 6:
        raise ValueError("Expected the generated multi-resolution ICO")
    binary = Path(executable).read_bytes()
    sizes = []
    for index in range(count):
        width, height, _, _, _, _, length, offset = struct.unpack_from(
            "<BBBBHHII", ico, 6 + 16 * index
        )
        if not length or offset < 6 + 16 * count or offset + length > len(ico):
            raise ValueError("Malformed ICO frame")
        if ico[offset : offset + length] not in binary:
            raise ValueError("Windows EXE is missing an approved icon frame")
        sizes.append([width or 256, height or 256])
    return {
        "schema": 1,
        "status": "passed",
        "macICNSExact": True,
        "windowsAllIconFramesEmbedded": True,
        "windowsSizes": sizes,
        "windowsNativeExecution": "pending",
        "releaseReady": False,
    }


def generate():
    icons = ROOT / "apps/native-shell/src-tauri/icons"
    source = icons / "icon-source.png"
    if source.is_symlink() or not source.is_file():
        raise ValueError("A regular approved icon-source.png is required")
    # Use the repository's locked Tauri CLI; no package download or OS changes.
    with tempfile.TemporaryDirectory(prefix="ptcg-icons-") as temporary:
        subprocess.run(
            [
                "node",
                str(ROOT / "node_modules/@tauri-apps/cli/tauri.js"),
                "icon",
                str(source),
                "--output",
                temporary,
            ],
            cwd=ROOT,
            check=True,
        )
        for name in FILES:
            target = icons / name
            if target.is_symlink():
                raise ValueError("Icon output must not be a symlink")
            shutil.copy2(Path(temporary) / name, target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-app", type=Path)
    parser.add_argument("--verify-exe", type=Path)
    args = parser.parse_args()
    if args.verify_app or args.verify_exe:
        if not (args.verify_app and args.verify_exe):
            parser.error("Provide both --verify-app and --verify-exe")
        print(json.dumps(verify(args.verify_app, args.verify_exe)))
    else:
        generate()

"""Install current artwork into the generated Xcode catalog, never a template logo."""

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import struct

SOURCE = "apps/native-shell/src-tauri/icons/icon-source.png"
BACKGROUND = "#f4efff"


def install(generated, apple_project, source):
    generated, source = Path(generated), Path(source)
    catalog = Path(apple_project) / "Assets.xcassets/AppIcon.appiconset"
    descriptor = catalog / "Contents.json"
    if not descriptor.is_file() or descriptor.is_symlink() or catalog.is_symlink():
        raise ValueError("Generated Xcode AppIcon catalog is required")
    if not source.is_file() or source.is_symlink():
        raise ValueError("Current master icon is required")
    images = json.loads(descriptor.read_text())["images"]
    pending, sizes = {}, set()
    for image in images:
        name = image.get("filename", "")
        if not name or Path(name).name != name or "\\" in name or not name.endswith(".png"):
            raise ValueError("Unexpected Xcode AppIcon filename")
        path, target = generated / name, catalog / name
        if not path.is_file() or path.is_symlink() or target.is_symlink():
            raise ValueError("Missing generated AppIcon: " + name)
        width, height = (Decimal(value) for value in image["size"].split("x"))
        scale = Decimal(image["scale"].removesuffix("x"))
        pixels = (width * scale, height * scale)
        raw = path.read_bytes()
        if (
            len(raw) < 33
            or raw[:8] != b"\x89PNG\r\n\x1a\n"
            or raw[12:16] != b"IHDR"
            or struct.unpack_from(">II", raw, 16) != pixels
        ):
            raise ValueError("Wrong generated AppIcon dimensions: " + name)
        pending[name] = raw
        sizes.add((image["idiom"], pixels))
    if not {
        ("iphone", (120, 120)), ("iphone", (180, 180)),
        ("ipad", (152, 152)), ("ipad", (167, 167)),
        ("ios-marketing", (1024, 1024)),
    } <= sizes:
        raise ValueError("Incomplete iPhone/iPad/marketing AppIcon catalog")
    # Validate the whole set before overwriting any template image.
    for name, raw in pending.items():
        (catalog / name).write_bytes(raw)
    return {
        "source": SOURCE,
        "sourceSha256": sha256(source.read_bytes()).hexdigest(),
        "background": BACKGROUND,
        "files": {name: sha256(raw).hexdigest() for name, raw in pending.items()},
    }

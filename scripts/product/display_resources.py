"""Build an explicitly selected display-only ZIP; no user databases or Git needed."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.sync.display import manifest, raster, safe, MAX_UNPACKED  # noqa: E402
from ptcg_engine_provider.catalog import MemoryCatalog  # noqa: E402


def build(source, catalog, output):
    source = Path(source).absolute()
    output = Path(output).absolute()
    if output.exists() or any(
        p.is_symlink() or p.is_junction() for p in (output, *output.parents)
    ):
        raise ValueError("Choose a NEW output file")
    raw = safe(source, "DISPLAY.json").read_bytes()
    known = {c["printingId"]: c for c in catalog["cards"]}
    value, images = manifest(
        raw, MemoryCatalog.from_catalog(catalog).catalog_version, known
    )
    if set(
        p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()
    ) != {"DISPLAY.json"} | {"files/" + key for key in images}:
        raise ValueError("Only declared display files may be exported")
    if len(raw) + sum(i["size"] for i in images.values()) > MAX_UNPACKED:
        raise ValueError("Resource pack too large")
    data = {}
    for key, image in images.items():
        path = safe(source, "files/" + key)
        if path.stat().st_size != image["size"]:
            raise ValueError("Image size mismatch")
        content = path.read_bytes()
        if sha256(content).hexdigest() != key or raster(content) != image["mime"]:
            raise ValueError("Image hash mismatch")
        data["files/" + key] = content
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DISPLAY.json", raw)
        for name, content in sorted(data.items()):
            archive.writestr(name, content)
    pin = sha256(output.read_bytes()).hexdigest()
    return {
        "sha256": pin,
        "cards": len(value["cards"]),
        "images": len(images),
        "displayOnly": True,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(
        json.dumps(
            build(a.source, json.loads(a.catalog.read_text(encoding="utf-8")), a.output)
        )
    )

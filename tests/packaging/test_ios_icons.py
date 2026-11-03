"""Missing or stale Xcode icons must not silently ship the Tauri template."""

from hashlib import sha256
import json
import struct
import zlib

import pytest

from scripts.packaging.ios_icons import install


def png(size):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\x80\x40\xc0" * size) * size))
        + chunk(b"IEND", b"")
    )


def fixture(tmp_path):
    project, generated = tmp_path / "apple", tmp_path / "generated"
    catalog = project / "Assets.xcassets/AppIcon.appiconset"
    catalog.mkdir(parents=True); generated.mkdir()
    images = []
    for idiom, size, scale in [
        ("iphone", "60", "2"), ("iphone", "60", "3"),
        ("ipad", "76", "2"), ("ipad", "83.5", "2"),
        ("ios-marketing", "1024", "1"),
    ]:
        name = f"AppIcon-{idiom}-{size}@{scale}x.png"
        images.append(dict(idiom=idiom, size=f"{size}x{size}", scale=scale + "x", filename=name))
        (generated / name).write_bytes(png(int(float(size) * int(scale))))
        (catalog / name).write_bytes(b"old template icon")
    (catalog / "Contents.json").write_text(json.dumps(dict(images=images, info=dict(version=1, author="xcode"))))
    source = tmp_path / "master.png"; source.write_bytes(png(1024))
    return project, generated, catalog, source, images


def test_replaces_every_xcode_slot_and_binds_the_master_hash(tmp_path):
    project, generated, catalog, source, images = fixture(tmp_path)
    before = (catalog / "Contents.json").read_bytes()
    report = install(generated, project, source)
    assert report["sourceSha256"] == sha256(source.read_bytes()).hexdigest()
    assert len(report["files"]) == len(images)
    for row in images:
        raw = (catalog / row["filename"]).read_bytes()
        assert raw == (generated / row["filename"]).read_bytes()
        assert report["files"][row["filename"]] == sha256(raw).hexdigest()
    assert (catalog / "Contents.json").read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "wrong-size", "missing-marketing", "path"])
def test_incomplete_icons_fail_before_any_template_is_overwritten(tmp_path, damage):
    project, generated, catalog, source, images = fixture(tmp_path)
    last = images[-1]["filename"]
    if damage == "missing":
        (generated / last).unlink()
    elif damage == "wrong-size":
        (generated / last).write_bytes(png(512))
    else:
        if damage == "missing-marketing":
            images.pop()
        else:
            images[-1]["filename"] = "../master.png"
        (catalog / "Contents.json").write_text(json.dumps(dict(images=images)))
    with pytest.raises(ValueError):
        install(generated, project, source)
    assert all(path.read_bytes() == b"old template icon" for path in catalog.glob("*.png"))

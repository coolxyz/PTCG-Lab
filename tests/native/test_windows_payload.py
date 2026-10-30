"""Offline Windows input gates, independent of target execution and real card data."""

from hashlib import sha256
import json
import struct
import sys
import tarfile
import zipfile

import pytest
from scripts.native.windows_payload import (
    verified_wheels,
    wheel_info,
    pe_machine,
    extract_runtime,
)
from scripts.native import windows_payload_check


def wheel(root, name="synthetic", version="1.0", requires=(), tag="py3-none-any"):
    root.mkdir(exist_ok=True)
    path = root / f"{name}-{version}-{tag}.whl"
    metadata = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: >=3.14\n"
        + "".join(f"Requires-Dist: {r}\n" for r in requires)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata)
        archive.writestr(
            f"{name}-{version}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: " + tag + "\n",
        )
    return {
        "name": name,
        "version": version,
        "filename": path.name,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def lock(*wheels):
    return {
        "schema": 1,
        "platform": "win_amd64",
        "python": "3.14.10",
        "abi": "cp314",
        "wheels": list(wheels),
    }


def test_closure_uses_windows_markers_and_exact_versions(tmp_path):
    house = tmp_path / "house"
    item = wheel(
        house,
        requires=[
            'colorama>=0.4; sys_platform == "win32"',
            'not_windows; sys_platform != "win32"',
        ],
    )
    with pytest.raises(ValueError, match="colorama"):
        verified_wheels(house, lock(item))
    dependency = wheel(house, "colorama", "0.4.6")
    assert len(verified_wheels(house, lock(item, dependency))) == 2
    dependency["version"] = "0.4.5"
    with pytest.raises(ValueError, match="identity"):
        verified_wheels(house, lock(item, dependency))


@pytest.mark.parametrize("kind", ["hash", "abi", "extra", "python", "escape"])
def test_untrusted_or_wrong_target_inputs_are_rejected_before_install(tmp_path, kind):
    house = tmp_path / "house"
    item = wheel(house, tag="cp314-cp314-win32" if kind == "abi" else "py3-none-any")
    value = lock(item)
    if kind == "hash":
        (house / item["filename"]).write_bytes(b"changed")
    elif kind == "extra":
        (house / "unapproved.whl").write_bytes(b"unapproved")
    elif kind == "python":
        value["python"] = "3.13.0"
    elif kind == "escape":
        item["filename"] = "../outside.whl"
    with pytest.raises(ValueError):
        verified_wheels(house, value)


def test_wheel_path_traversal_and_links_are_rejected(tmp_path):
    path = tmp_path / "malicious.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../outside.py", "bad")
    with pytest.raises(ValueError, match="paths"):
        wheel_info(path)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("x.dist-info/METADATA", "Name: x\nVersion: 1\n")
        link = zipfile.ZipInfo("linked")
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "outside")
    with pytest.raises(ValueError, match="link"):
        wheel_info(path)


def test_pe_architecture_gate_rejects_x86_or_non_pe(tmp_path):
    raw = bytearray(128)
    raw[:2] = b"MZ"
    struct.pack_into("<I", raw, 60, 64)
    raw[64:68] = b"PE\0\0"
    struct.pack_into("<H", raw, 68, 0x8664)
    file = tmp_path / "synthetic.exe"
    file.write_bytes(raw)
    assert pe_machine(file) == "AMD64"
    struct.pack_into("<H", raw, 68, 0x14C)
    file.write_bytes(raw)
    with pytest.raises(ValueError, match="x64"):
        pe_machine(file)
    file.write_bytes(b"not a PE")
    with pytest.raises(ValueError):
        pe_machine(file)


def test_runtime_hash_and_archive_links_fail_before_extraction(tmp_path):
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("python/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        tar.addfile(link)
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="hash"):
        extract_runtime(archive, output, {"sha256": "0" * 64})
    with pytest.raises(ValueError, match="Unsafe"):
        extract_runtime(
            archive, output, {"sha256": sha256(archive.read_bytes()).hexdigest()}
        )
    assert not output.exists() and not (tmp_path / "outside").exists()


def test_payload_source_binding_rejects_tamper_extras_and_unmatched_source(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(windows_payload_check, "source_digest", lambda root: "current")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "BUILD-SOURCE.json").write_text(
        json.dumps({"nativeSourceSha256": "current"})
    )
    (payload / "python").mkdir()
    (payload / "python/python.exe").write_bytes(b"synthetic")
    (payload / "bootstrap.py").write_text("# synthetic")
    files = {
        p.relative_to(payload).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in payload.rglob("*")
        if p.is_file()
    }
    meta = {
        "schema": 1,
        "platform": "windows-x64",
        "python": "python/python.exe",
        "files": files,
    }
    (payload / "manifest.json").write_text(json.dumps(meta))
    assert windows_payload_check.verify(payload) == payload
    monkeypatch.setattr(
        windows_payload_check, "source_digest", lambda root: "unverified"
    )
    with pytest.raises(ValueError, match="rebuilt"):
        windows_payload_check.verify(payload)
    monkeypatch.setattr(windows_payload_check, "source_digest", lambda root: "current")
    (payload / "extra").write_text("extra")
    with pytest.raises(ValueError, match="Unexpected"):
        windows_payload_check.verify(payload)
    (payload / "extra").unlink()
    (payload / "python/python.exe").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Changed"):
        windows_payload_check.verify(payload)


@pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows refusal contract")
def test_windows_exe_builder_never_executes_cross_payload_on_mac(tmp_path):
    from scripts.packaging.windows import build

    with pytest.raises(ValueError, match="native Windows"):
        build(tmp_path / "payload", tmp_path / "release")
    assert not (tmp_path / "release").exists()

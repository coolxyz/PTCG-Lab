"""Trim only sealed build payloads; retain actual runtime dependencies."""

from hashlib import sha256
import json
from pathlib import Path

import pytest
from scripts.packaging.runtime_trim import trim


def fixture(tmp_path, *, dependency=False):
    root = Path(__file__).resolve().parents[2]
    p = tmp_path / "payload"
    site = p / "python/lib/python3.14/site-packages"
    site.mkdir(parents=True)
    for name in (
        "pytest",
        "pip",
        "ruff",
        "setuptools",
        "iniconfig",
        "pluggy",
        "packaging",
        "httpx",
    ):
        folder = site / (name + "-1.dist-info")
        folder.mkdir()
        (folder / "METADATA").write_text(
            f"Name: {name}\nVersion: 1\n"
            + ("Requires-Dist: pytest\n" if dependency and name == "httpx" else "")
        )
        (site / name).mkdir()
        (site / name / "__init__.py").write_text("# synthetic")
    (p / "python/bin").mkdir()
    (p / "python/bin/python3").write_bytes(b"fake-binary")
    (p / "bootstrap.py").write_text("# synthetic")
    (p / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "platform": "macos-arm64",
                "python": "python/bin/python3",
                "files": {
                    f.relative_to(p).as_posix(): sha256(f.read_bytes()).hexdigest()
                    for f in p.rglob("*")
                    if f.is_file()
                },
            }
        )
    )
    return root, p


def test_trim_closes_manifest_and_keeps_worker_httpx(tmp_path):
    root, payload = fixture(tmp_path)
    result = trim(payload, root=root)
    assert (
        result["remainingDistributions"] == ["httpx"]
        and result["externalGitRequired"] is False
    )
    assert not (payload / "python/lib/python3.14/site-packages/pytest").exists()
    assert (payload / "app/scripts/sync/verify_runtime.py").is_file()
    manifest = json.loads((payload / "manifest.json").read_text())
    assert "RUNTIME-CLOSURE.json" in manifest["files"]


def test_required_runtime_dependency_cannot_be_silently_removed(tmp_path):
    root, payload = fixture(tmp_path, dependency=True)
    with pytest.raises(ValueError, match="pytest"):
        trim(payload, root=root)


def test_tampered_payload_is_refused_before_trim(tmp_path):
    root, payload = fixture(tmp_path)
    (payload / "bootstrap.py").write_text("changed")
    with pytest.raises(ValueError, match="Changed payload"):
        trim(payload, root=root)
    assert (payload / "python/lib/python3.14/site-packages/pytest").exists()

"""Pinned resource transfer, offline reads, retention and kit integrity regressions."""

from hashlib import sha1, sha256
import io
import json
import sqlite3
from types import SimpleNamespace

import pytest
from PIL import Image
from scripts.product.resources import export_pack, install_pack
from scripts.product.manage_kit import verify, configuration


def seed(home, commit="a" * 40, color="red"):
    data = io.BytesIO()
    Image.new("RGB", (9, 12), color).save(data, "PNG")
    raw = data.getvalue()
    digest = sha256(raw).hexdigest()
    blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    (home / "images").mkdir(parents=True, exist_ok=True)
    (home / "images" / digest).write_bytes(raw)
    with sqlite3.connect(home / "images/index.sqlite") as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS images(commit_id TEXT,card_id TEXT,sha TEXT,mime TEXT)"
        )
        db.execute(
            "INSERT INTO images VALUES(?,?,?,?)", (commit, "123", digest, "image/png")
        )
    snapshot = home / "snapshots" / commit
    snapshot.mkdir(parents=True)
    (snapshot / "image-index.json").write_text(
        json.dumps({"123": {"blob": blob, "path": "private-path-not-exported.png"}})
    )
    return digest, raw


def test_transfer_offline_restart_and_keep_old_version(tmp_path):
    from packages.sync.images import Images

    source, target, pack = (tmp_path / n for n in ("source", "target", "pack"))
    first, raw = seed(source)
    (source / "credentials.json").write_text("never export")
    assert export_pack(source, pack)["images"] == 1
    assert not list(pack.rglob("*.sqlite"))
    assert "private-path" not in (pack / "RESOURCES.json").read_text()
    assert not (pack / "credentials.json").exists()
    install_pack(pack, target)
    install_pack(pack, target)  # idempotent
    assert export_pack(target, tmp_path / "reexport")["images"] == 1

    def offline(*args):
        raise AssertionError("An installed image must never reach the source")

    images = Images(
        target,
        SimpleNamespace(validate_sha=lambda _: None, initialize=offline, blob=offline),
    )
    assert images.get("a" * 40, "123")[0].read_bytes() == raw
    second, _ = seed(source, "b" * 40, "blue")
    export_pack(source, tmp_path / "update")
    install_pack(tmp_path / "update", target)
    assert (target / "images" / first).exists() and (
        target / "images" / second
    ).exists()
    restarted = Images(target, images.source)
    assert restarted.get("a" * 40, "123")[0].read_bytes() == raw


def test_bad_pack_changes_no_cache_and_conflicting_version_is_rejected(tmp_path):
    source, target, pack = (tmp_path / n for n in ("source", "target", "pack"))
    digest, raw = seed(source)
    export_pack(source, pack)
    (pack / "images" / digest).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        install_pack(pack, target)
    assert not target.exists()
    (pack / "images" / digest).write_bytes(raw)
    install_pack(pack, target)
    with sqlite3.connect(target / "images/index.sqlite") as db:
        db.execute("UPDATE images SET sha=?", ("b" * 64,))
    with pytest.raises(ValueError, match="conflicts"):
        install_pack(pack, target)
    assert (target / "images" / digest).read_bytes() == raw


def test_kit_rejects_tampering_traversal_and_linked_parent(tmp_path):
    (tmp_path / "app").mkdir()
    file = tmp_path / "app/code.py"
    file.write_text("safe")
    manifest = {
        "schema": 1,
        "stage": "P5.5-D",
        "files": {"app/code.py": sha256(b"safe").hexdigest()},
    }

    def write():
        (tmp_path / "KIT-MANIFEST.json").write_text(json.dumps(manifest))

    write()
    verify(tmp_path)
    file.write_text("changed")
    with pytest.raises(ValueError, match="Changed"):
        verify(tmp_path)
    manifest["files"] = {"../escape": "a" * 64}
    write()
    with pytest.raises(ValueError, match="Unsafe"):
        verify(tmp_path)
    manifest["files"] = {"linked/code.py": sha256(b"changed").hexdigest()}
    (tmp_path / "linked").symlink_to(tmp_path / "app", target_is_directory=True)
    write()
    with pytest.raises(ValueError, match="linked"):
        verify(tmp_path)


def test_config_stable_for_reopen_and_trust_change_requires_review(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "connection.json").write_text(
        json.dumps({"origin": "https://example.test"})
    )
    first = configuration(tmp_path)
    raw = first.read_bytes()
    assert configuration(tmp_path).read_bytes() == raw
    assert first.stat().st_mode & 0o077 == 0
    (tmp_path / "connection.json").write_text(
        json.dumps({"origin": "https://different.test"})
    )
    with pytest.raises(ValueError, match="trust changed"):
        configuration(tmp_path)
    assert first.read_bytes() == raw


def test_cover_upgrade_installs_only_manifest_wheels(tmp_path, monkeypatch):
    from scripts.product import manage_kit

    app = tmp_path / "app"
    (app / ".venv/bin").mkdir(parents=True)
    (app / ".venv/bin/python").write_text("placeholder")
    (tmp_path / "wheels").mkdir()
    for name in ("client-old.whl", "client-new.whl"):
        (tmp_path / "wheels" / name).write_text("placeholder")
    (tmp_path / "connection.json").write_text(
        json.dumps({"origin": "https://example.test"})
    )
    monkeypatch.setattr(
        manage_kit, "verify", lambda _: {"files": {"wheels/client-new.whl": "digest"}}
    )
    monkeypatch.setattr(manage_kit.shutil, "which", lambda _: "git")
    calls = []
    monkeypatch.setattr(
        manage_kit.subprocess, "run", lambda command, **kw: calls.append(command)
    )
    manage_kit.install(tmp_path)
    assert str(tmp_path / "wheels/client-new.whl") in calls[0]
    assert str(tmp_path / "wheels/client-old.whl") not in calls[0]

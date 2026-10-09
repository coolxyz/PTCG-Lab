"""Offline, hash-verified bundles including every retained executable release."""
from contextlib import closing
import hashlib
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile

from .common import SyncError, inside, read, write, now
from .releases import Releases


def export_bundle(service, output):
    output = Path(output).resolve()
    if output.exists():
        raise SyncError("BUNDLE_EXISTS", "备份目标已存在，未覆盖")
    with service.jobs.lease(), tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        for folder in ("releases", "snapshots", "results", "verifications", "images"):
            source = service.home / folder
            if source.exists():
                shutil.copytree(source, stage / "sync" / folder, ignore=shutil.ignore_patterns("*.sqlite", "*.sqlite-wal", "*.sqlite-shm", "__pycache__", "*.pyc"))
        # Release SQLite files are immutable and are part of release hashes.
        for source in (service.home / "releases").glob("*/data/catalog/catalog.sqlite"):
            shutil.copy2(source, stage / "sync" / source.relative_to(service.home))
        for name in ("baseline.json", "current.json", "identity-archive.json", "last-check.json", "retired.json", "repository.json"):
            if (service.home / name).exists():
                shutil.copy2(service.home / name, stage / "sync" / name)
        for source, target in ((service.user_db, stage / "user.sqlite"), (service.jobs.path, stage / "sync/jobs.sqlite"), (service.images.database, stage / "sync/images/index.sqlite")):
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise SyncError("BACKUP_FAILED", "数据库备份不完整")
        uploads = service.user_db.parent / (service.user_db.stem + "-card-images")
        if uploads.exists():
            shutil.copytree(uploads, stage / "user-card-images")
        files = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.rglob("*") if p.is_file()}
        write(stage / "bundle.json", {"schema": 1, "createdAt": now(), "files": files})
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
            for p in stage.rglob("*"):
                if p.is_file():
                    archive.write(p, p.relative_to(stage).as_posix())
    return {"path": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def restore_bundle(bundle, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise SyncError("RESTORE_NOT_EMPTY", "恢复目录必须不存在，避免覆盖用户资料")
    # Validate all paths and bytes before creating the destination.
    with zipfile.ZipFile(bundle) as archive:
        import json
        manifest = json.loads(archive.read("bundle.json"))
        if manifest.get("schema") != 1 or set(archive.namelist()) != set(manifest["files"]) | {"bundle.json"}:
            raise SyncError("BUNDLE_INVALID", "备份清单不完整")
        for name, expected in manifest["files"].items():
            inside(destination, name)
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise SyncError("BUNDLE_CORRUPT", "备份内容校验失败")
        destination.mkdir(parents=True)
        for name in archive.namelist():
            target = inside(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    releases = Releases(destination / "sync")
    for release in releases.list():
        releases.verify(release["releaseId"])
    return {"home": str(destination / "sync"), "database": str(destination / "user.sqlite"), "currentRelease": releases.current()}

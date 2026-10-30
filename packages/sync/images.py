"""Pinned sync-snapshot images use the same Raw/CDN cache as bundled artwork."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import threading

from .artwork import ArtworkCache, MAX_IMAGE
from .common import SyncError
from .display import raster
from .http_source import HTTPSource, object_sha


class Images:
    def __init__(self, home, source, *, resolver=None, cache=None):
        self.home, self.source = Path(home), source
        self.resolver = resolver
        self.indexes, self.index_lock = {}, threading.RLock()
        self.cache = cache or ArtworkCache(self.home)
        # Existing verified image files remain available offline. They are not
        # deleted or converted when the new shared cache is introduced.
        self.directory = self.home / "images"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "index.sqlite"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS images(commit_id TEXT, card_id TEXT, sha TEXT, mime TEXT, PRIMARY KEY(commit_id,card_id))"
            )

    def index(self, commit):
        HTTPSource.validate_sha(commit)
        path = self.cache.safe(self.home / "snapshots" / commit / "image-index.json")
        if not path.is_file():
            return {}
        if path.stat().st_size > 16 * 1024**2:
            raise SyncError("IMAGE_SNAPSHOT_MISSING", "图片索引过大", 404)
        stamp = (path.stat().st_mtime_ns, path.stat().st_size)
        with self.index_lock:
            old = self.indexes.get(commit)
            if old and old[0] == stamp:
                return old[1]
            from .http_source import parse

            value = parse(path.read_bytes())
            if not isinstance(value, dict):
                raise SyncError("IMAGE_SNAPSHOT_MISSING", "图片索引格式无效", 404)
            if len(self.indexes) >= 4:
                self.indexes.pop(next(iter(self.indexes)))
            self.indexes[commit] = (stamp, value)
            return value

    def available(self, commit, card_id):
        try:
            value = self.index(commit).get(card_id)
            if (
                not isinstance(value, dict)
                or not value.get("blob")
            ):
                return False
            HTTPSource.validate_sha(value["blob"])
            if value.get("path"):
                HTTPSource.validate_path(value["path"])
                return True
            self.data(commit, card_id)  # Pathless imports can only return verified local bytes.
            return True
        except SyncError:
            return False

    def data(self, commit, card_id):
        HTTPSource.validate_sha(commit)
        if not isinstance(card_id, str) or not card_id.isdecimal():
            raise SyncError("INVALID_CARD_ID", "卡图编号无效")
        path = self.home / "snapshots" / commit / "image-index.json"
        if not path.exists():
            raise SyncError("IMAGE_SNAPSHOT_MISSING", "该版本图片索引不存在", 404)
        entry = self.index(commit).get(card_id)
        if not entry or not entry.get("blob"):
            raise SyncError("IMAGE_MISSING", "上游未提供对应图片", 404)
        if entry.get("path"):
            HTTPSource.validate_path(entry["path"])
        HTTPSource.validate_sha(entry["blob"])
        with closing(sqlite3.connect(self.database)) as db:
            old = db.execute(
                "SELECT sha,mime FROM images WHERE commit_id=? AND card_id=?",
                (commit, card_id),
            ).fetchone()
        if old:
            cached = self.cache.safe(self.directory / old[0])
            if cached.is_file() and cached.stat().st_size <= MAX_IMAGE:
                data = cached.read_bytes()
                if (
                    sha256(data).hexdigest() == old[0]
                    and object_sha("blob", data) == entry["blob"]
                ):
                    return data, raster(data)
        if not entry.get("path"):
            raise SyncError("IMAGE_MISSING", "离线图片缺失或已损坏", 404)
        source = self.resolver(commit) if self.resolver else self.source
        # The immutable snapshot already provides the expected Git blob. No
        # tree/blob REST API request is needed when an image becomes visible.
        return self.cache.image(source.repository, commit, entry["path"], entry["blob"])

    def get(self, commit, card_id):
        data, mime = self.data(commit, card_id)
        sha = sha256(data).hexdigest()
        shared = self.cache.directory / sha
        return (shared if shared.is_file() else self.directory / sha), mime

    def status(self, commit):
        HTTPSource.validate_sha(commit)
        entries = self.index(commit)
        with closing(self.cache.db()) as db:
            blobs = {row[0] for row in db.execute("SELECT blob FROM blobs")}
        total = sum(bool(v.get("blob")) for v in entries.values())
        cached = sum(v.get("blob") in blobs for v in entries.values())
        return {
            "total": total,
            "cached": cached,
            "pending": max(0, total - cached),
            "mode": "on-demand-resumable",
        }

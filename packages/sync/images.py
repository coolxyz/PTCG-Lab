"""Commit-pinned image cache with decoded format/pixel validation."""
import hashlib
import io
from pathlib import Path
import sqlite3
import threading
import uuid
from contextlib import closing

from PIL import Image

from .common import SyncError, read


class Images:
    def __init__(self, home, source, *, resolver=None):
        self.home, self.source = Path(home), source
        self.resolver = resolver
        self.directory = self.home / "images"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "index.sqlite"
        self.lock = threading.Semaphore(2)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS images(commit_id TEXT, card_id TEXT, sha TEXT, mime TEXT, PRIMARY KEY(commit_id,card_id))")

    def get(self, commit, card_id):
        self.source.validate_sha(commit)
        if not card_id.isdecimal():
            raise SyncError("INVALID_CARD_ID", "卡图编号无效")
        with self.lock:
            with closing(sqlite3.connect(self.database)) as db:
                row = db.execute("SELECT sha,mime FROM images WHERE commit_id=? AND card_id=?", (commit, card_id)).fetchone()
            if row and (self.directory / row[0]).is_file():
                return self.directory / row[0], row[1]
            path = self.home / "snapshots" / commit / "image-index.json"
            if not path.exists():
                raise SyncError("IMAGE_SNAPSHOT_MISSING", "该版本图片索引不存在", 404)
            index = read(path)
            entry = index.get(card_id)
            if not entry or not entry.get("path") or not entry.get("blob"):
                raise SyncError("IMAGE_MISSING", "上游未提供对应图片", 404)
            source = self.resolver(commit) if self.resolver else self.source
            source.initialize()
            data = source.blob(commit, entry["path"])
            if len(data) > 8 * 1024 * 1024:
                raise SyncError("IMAGE_TOO_LARGE", "上游图片超过大小限制")
            # Git blob identity binds these exact bytes to the pinned tree.
            git_hash = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data).hexdigest()
            if git_hash != entry["blob"]:
                raise SyncError("IMAGE_HASH_MISMATCH", "卡图内容与固定提交不一致")
            try:
                with Image.open(io.BytesIO(data)) as image:
                    if image.width * image.height > 20_000_000 or image.format not in ("PNG", "JPEG", "WEBP"):
                        raise ValueError("invalid image")
                    mime = Image.MIME[image.format]
                    image.verify()
            except Exception as exc:
                raise SyncError("IMAGE_INVALID", "上游卡图未通过图像校验") from exc
            sha = hashlib.sha256(data).hexdigest()
            output = self.directory / sha
            temp = self.directory / (sha + "." + uuid.uuid4().hex + ".tmp")
            temp.write_bytes(data)
            temp.replace(output)
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("INSERT OR REPLACE INTO images VALUES(?,?,?,?)", (commit, card_id, sha, mime))
            return output, mime

    def status(self, commit):
        with closing(sqlite3.connect(self.database)) as db:
            cached = db.execute("SELECT COUNT(*) FROM images WHERE commit_id=?", (commit,)).fetchone()[0]
        path = self.home / "snapshots" / commit / "image-index.json"
        total = sum(bool(v.get("blob")) for v in read(path).values()) if path.exists() else 0
        return {"total": total, "cached": cached, "pending": max(0, total-cached), "mode": "on-demand-resumable"}

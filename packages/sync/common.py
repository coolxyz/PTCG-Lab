from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import uuid
import time

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "https://github.com/duanxr/PTCG-CHS-Datasets"


class SyncError(ValueError):
    def __init__(self, code, message, status=422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("wb") as f:
            f.write(encoded(value))
            f.flush()
            os.fsync(f.fileno())
        # Windows readers may briefly hold a file without delete sharing. Keep
        # atomic replacement, retrying only this transient sharing violation.
        for attempt in range(50):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 49:
                    raise SyncError("LOCAL_FILE_BUSY", "本地文件暂被占用，原版本保持不变，请重试", 503)
                time.sleep(.05)
    finally:
        tmp.unlink(missing_ok=True)


def inside(root, relative):
    root = Path(root).resolve()
    p = (root / relative).resolve()
    if not p.is_relative_to(root) or p == root:
        raise SyncError("INVALID_PATH", "文件路径超出缓存范围")
    return p

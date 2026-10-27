"""Only the currently activated release may execute; saved frames remain data."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing

from .common import SyncError
from .releases import RULE_POLICY


class Worker:
    def __init__(self, releases, release_id, user_db):
        manifest = releases.verify(release_id)
        if manifest.get("metadata", {}).get("rulePolicy") != RULE_POLICY:
            raise SyncError("RULE_RELEASE_UNAVAILABLE", "旧规则发布不再执行，请重新发布当前规则", 409)
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        log = releases.home / "logs" / (release_id + ".log")
        log.parent.mkdir(parents=True, exist_ok=True)
        self.log = log.open("ab")
        self.process = subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).with_name("worker.py")), str(releases.path(release_id).resolve()), str(Path(user_db).resolve()), release_id, str((releases.home / "identity-archive.json").resolve())], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def request(self, method, path, headers=None, body=b"", timeout=90):
        with self.lock:
            if self.process.poll() is not None:
                raise SyncError("WORKER_UNAVAILABLE", "版本工作进程已退出，可重试", 503)
            self.process.stdin.write(json.dumps({"method": method, "path": path, "headers": headers or {}, "body": base64.b64encode(body).decode()}) + "\n")
            self.process.stdin.flush()
            try:
                line = self.executor.submit(self.process.stdout.readline).result(timeout=timeout)
                response = json.loads(line)
                response["body"] = base64.b64decode(response["body"])
                return response
            except (TimeoutError, ValueError) as exc:
                self.close()
                raise SyncError("WORKER_TIMEOUT", "版本处理超时，已保留持久对局，可重试", 503) from exc

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.log.close()


class RuntimePool:
    def __init__(self, releases, user_db):
        self.releases, self.user_db = releases, str(user_db)
        self.workers = {}
        self.lock = threading.RLock()

    def get(self, release_id):
        with self.lock:
            if release_id is None or release_id != self.releases.current():
                raise SyncError("RULE_RELEASE_UNAVAILABLE", "仅当前规则发布可执行，请开始新对局", 409)
            for key in list(self.workers):
                if key != release_id:
                    self.workers.pop(key).close()
            old = self.workers.get(release_id)
            if old and old.process.poll() is not None:
                old.close()
                del self.workers[release_id]
            if release_id not in self.workers:
                self.workers[release_id] = Worker(self.releases, release_id, self.user_db)
            return self.workers[release_id]

    def route(self, path, body=b"", token=None):
        current = self.releases.current()
        if not current:
            return None
        parts = path.split("?", 1)[0].split("/")
        mid = parts[4] if len(parts) >= 5 and parts[1:4] == ["api", "battle", "matches"] else None
        if mid and Path(self.user_db).exists():
            with closing(sqlite3.connect(self.user_db)) as db:
                row = db.execute("SELECT body FROM matches WHERE id=?", (mid,)).fetchone()
            if row:
                pinned = json.loads(row[0]).get("config", {}).get("provenance", {}).get("syncReleaseId")
                return current if pinned == current else None
        return current

    def close(self):
        with self.lock:
            for worker in self.workers.values():
                worker.close()
            self.workers.clear()

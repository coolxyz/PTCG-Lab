"""One process per immutable release; requests never share mutable registries."""
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

from .common import SyncError, read, write


class Worker:
    def __init__(self, releases, release_id, user_db):
        releases.verify(release_id)
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
            old = self.workers.get(release_id)
            if old and old.process.poll() is not None:
                old.close()
                del self.workers[release_id]
            if release_id not in self.workers:
                # Idle process eviction does not delete frozen runtime files.
                if len(self.workers) >= 4:
                    for key, worker in list(self.workers.items()):
                        if worker.lock.acquire(blocking=False):
                            try:
                                worker.close()
                                del self.workers[key]
                            finally:
                                worker.lock.release()
                            break
                self.workers[release_id] = Worker(self.releases, release_id, self.user_db)
            return self.workers[release_id]

    def route(self, path, body=b"", token=None):
        current = self.releases.current()
        if not current and not (self.releases.home / "baseline.json").exists():
            return None
        parts = path.split("?", 1)[0].split("/")
        mid = parts[4] if len(parts) >= 5 and parts[1:4] == ["api", "battle", "matches"] else None
        if mid and Path(self.user_db).exists():
            with closing(sqlite3.connect(self.user_db)) as db:
                row = db.execute("SELECT body FROM matches WHERE id=?", (mid,)).fetchone()
            if row:
                return self.saved_release(json.loads(row[0]))
        # An idempotent match creation retried after an upgrade must return the old
        # match from its original runtime, never reinterpret its revision.
        if path.split("?")[0] == "/api/battle/matches" and body and token:
            import hashlib
            try:
                request_id = json.loads(body).get("requestId")
                owner = hashlib.sha256(token.encode()).hexdigest()
                with closing(sqlite3.connect(self.user_db)) as db:
                    row = db.execute("SELECT body FROM matches WHERE owner=? AND request_id=?", (owner, request_id)).fetchone()
                if row:
                    return self.saved_release(json.loads(row[0]))
            except (ValueError, sqlite3.OperationalError):
                pass
        return current

    def saved_release(self, record):
        provenance = record.get("config", {}).get("provenance", {})
        if provenance.get("syncReleaseId"):
            release_id = provenance["syncReleaseId"]
            retired = self.releases.home / 'retired.json'
            if retired.exists() and release_id in read(retired):
                return None
            return release_id
        for manifest in self.releases.list():
            if manifest["catalogVersion"] == provenance.get("catalogVersion") and manifest["effectVersion"] == provenance.get("effectReleaseVersion"):
                return manifest["releaseId"]
        baseline = self.releases.home / "baseline.json"
        return read(baseline)["releaseId"] if baseline.exists() else None

    def close(self):
        with self.lock:
            for worker in self.workers.values():
                worker.close()
            self.workers.clear()

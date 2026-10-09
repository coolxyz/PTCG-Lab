"""Durable, cross-process leased jobs; failures never change the active release."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from .common import SyncError, now, encoded


class Jobs:
    def __init__(self, home):
        self.path = Path(home) / "jobs.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL, body TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,stage TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY,owner TEXT NOT NULL,expires TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activations(seq INTEGER PRIMARY KEY AUTOINCREMENT,release_id TEXT NOT NULL,previous TEXT,created_at TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, idem, body):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE idem=?", (idem,)).fetchone()
            if row:
                return self.decode(row)
            job_id = uuid.uuid4().hex
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (job_id, idem, "queued", "queued", encoded(body).decode(), 0, now()))
            return self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    @staticmethod
    def decode(row):
        return {**json.loads(row["body"]), "id": row["id"], "state": row["state"], "stage": row["stage"], "cancelled": bool(row["cancelled"]), "updatedAt": row["updated_at"]}

    def get(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise SyncError("JOB_NOT_FOUND", "同步任务不存在", 404)
            return self.decode(row)

    def list(self):
        with self.db() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 50")]

    def update(self, job_id, *, stage=None, state=None, **fields):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            body = {**json.loads(row["body"]), **fields}
            db.execute("UPDATE jobs SET stage=?,state=?,body=?,updated_at=? WHERE id=?", (stage or row["stage"], state or row["state"], encoded(body).decode(), now(), job_id))
            db.execute("INSERT INTO events(job_id,stage,body,created_at) VALUES(?,?,?,?)", (job_id, stage or row["stage"], encoded(fields).decode(), now()))

    def events(self, job_id, after=0):
        self.get(job_id)
        with self.db() as db:
            return [{**dict(r), "body": json.loads(r["body"])} for r in db.execute("SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT 100", (job_id, after))]

    def cancel(self, job_id):
        self.get(job_id)
        with self.db() as db:
            db.execute("UPDATE jobs SET cancelled=1 WHERE id=?", (job_id,))

    def active(self):
        with self.db() as db:
            row = db.execute("SELECT expires FROM leases WHERE name='sync'").fetchone()
            return bool(row and row["expires"] > now())

    def published(self):
        with self.db() as db:
            return {r[0] for r in db.execute("SELECT DISTINCT release_id FROM activations")}

    @contextmanager
    def lease(self, name="sync"):
        owner = uuid.uuid4().hex

        def expiry():
            return (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()

        with self.db() as db:
            row = db.execute("SELECT * FROM leases WHERE name=?", (name,)).fetchone()
            if row and row["expires"] > now():
                raise SyncError("SYNC_BUSY", "已有同步任务运行中", 409)
            db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,?)", (name, owner, expiry()))
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(20):
                with self.db() as db:
                    db.execute("UPDATE leases SET expires=? WHERE name=? AND owner=?", (expiry(), name, owner))

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=25)
            with self.db() as db:
                db.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))

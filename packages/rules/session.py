"""Single-writer local durable session, not a distributed match service.

The journal commits before acknowledgment. A failed save/step reconstructs from
the last committed replay; retries retain the original idempotency receipt.
"""
import json
import sqlite3
from packages.rules.adapter import Adapter


class Session:
    def __init__(self, path, **config):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS replay (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        row = self.db.execute('SELECT body FROM replay WHERE id=1').fetchone()
        if row:
            self.game = Adapter.replay(json.loads(row[0]))
        else:
            self.game = Adapter(**config)
            self._save()

    def _save(self):
        body = json.dumps(self.game.export_private_replay(), ensure_ascii=False)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO replay VALUES (1, ?)', (body,))

    def submit(self, viewer, command):
        try:
            receipt = self.game.submit(viewer, command)
            self._save()
            return receipt
        except Exception:
            self.db.rollback()
            row = self.db.execute('SELECT body FROM replay WHERE id=1').fetchone()
            self.game = Adapter.replay(json.loads(row[0]))
            raise

    def close(self):
        self.db.close()

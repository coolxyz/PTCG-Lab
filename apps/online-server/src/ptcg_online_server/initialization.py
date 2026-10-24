"""Claim a durable initialization, execute outside SQL, fence its final commit."""

import uuid
from psycopg.types.json import Jsonb
from .errors import ServiceError


class Initializer:
    def __init__(self, repository, worker):
        self.repo, self.worker = repository, worker

    def claim(self):
        with self.repo.transaction() as db:
            job = db.execute(
                """SELECT * FROM ptcg_v2.initializations
                WHERE rule_release_id=%s AND
                ((status='pending' AND retry_at<=now()) OR (status='running' AND lease_until<=now()))
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                (self.repo.settings.approved_rule_release_id,),
            ).fetchone()
            if not job:
                return None
            if job["attempts"] >= 3:
                self._abort(db, job)
                return None
            return db.execute(
                """UPDATE ptcg_v2.initializations SET status='running',attempts=attempts+1,
                lease_token=%s,lease_until=now()+interval '60 seconds' WHERE match_id=%s RETURNING *""",
                (uuid.uuid4().hex, job["match_id"]),
            ).fetchone()

    def _abort(self, db, job):
        db.execute(
            "UPDATE ptcg_v2.initializations SET status='technical_abort',lease_token=NULL,lease_until=NULL WHERE match_id=%s",
            (job["match_id"],),
        )
        db.execute(
            "UPDATE ptcg_v2.rooms SET status='technical_abort',version=version+1 WHERE id=%s",
            (job["room_id"],),
        )

    def finish(self, job, record, *, deferred=False):
        with self.repo.transaction() as db:
            current = db.execute(
                """SELECT * FROM ptcg_v2.initializations WHERE match_id=%s
                AND status='running' AND lease_token=%s AND lease_until>clock_timestamp() FOR UPDATE""",
                (job["match_id"], job["lease_token"]),
            ).fetchone()
            if not current:
                return False
            if deferred:
                db.execute(
                    """UPDATE ptcg_v2.initializations SET status='pending',attempts=attempts-1,
                    lease_token=NULL,lease_until=NULL,retry_at=now()+interval '2 seconds' WHERE match_id=%s""",
                    (job["match_id"],),
                )
                return False
            if record is None:
                if current["attempts"] >= 3:
                    self._abort(db, current)
                else:
                    db.execute(
                        """UPDATE ptcg_v2.initializations SET status='pending',lease_token=NULL,
                        lease_until=NULL,retry_at=now()+interval '2 seconds' WHERE match_id=%s""",
                        (job["match_id"],),
                    )
                return False
            db.execute(
                """UPDATE ptcg_v2.initializations SET status='initialized',recovery=%s,
                lease_token=NULL,lease_until=NULL WHERE match_id=%s""",
                (Jsonb(record), job["match_id"]),
            )
            db.execute(
                "UPDATE ptcg_v2.rooms SET status='initialized',version=version+1 WHERE id=%s",
                (job["room_id"],),
            )
            return True

    def tick(self):
        if not self.worker.available:
            return False
        job = self.claim()
        if not job:
            return False
        try:
            record = self.worker.initialize(job)
        except ServiceError as error:
            return self.finish(job, None, deferred=error.code == "CAPACITY_EXCEEDED")
        except Exception:
            record = None
        return self.finish(job, record)

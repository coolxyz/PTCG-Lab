"""Opaque bearer tokens; rotation/reuse detection serialized by session row lock."""

from datetime import timedelta
from hashlib import sha256
import re
import secrets
import uuid

from .errors import ServiceError
from .repository import ADMISSION_LOCK


def token_digest(token, kind):
    if not isinstance(token, str) or not re.fullmatch(
        kind + r"_[A-Za-z0-9_-]{43}", token
    ):
        raise ServiceError("UNAUTHORIZED", 401)
    return sha256(token.encode()).hexdigest()


def identity(row, server_id):
    return {
        "protocolVersion": "2.0",
        "serverId": server_id,
        "sessionId": row["id"],
        "userId": row["user_id"],
        "deviceId": row["device_id"],
    }


class Sessions:
    def __init__(self, repository):
        self.repo, self.settings = repository, repository.settings

    def _issue(self, db, session):
        now = db.execute("SELECT now() AS value").fetchone()["value"]
        access_expiry = min(
            now + timedelta(seconds=self.settings.access_seconds), session["expires_at"]
        )
        access, refresh = (
            "atk_" + secrets.token_urlsafe(32),
            "rtk_" + secrets.token_urlsafe(32),
        )
        db.execute(
            "DELETE FROM ptcg_v2.access_tokens WHERE session_id=%s", (session["id"],)
        )
        db.execute(
            "INSERT INTO ptcg_v2.access_tokens VALUES(%s,%s,%s)",
            (token_digest(access, "atk"), session["id"], access_expiry),
        )
        db.execute(
            "INSERT INTO ptcg_v2.refresh_tokens(digest,session_id,expires_at) VALUES(%s,%s,%s)",
            (token_digest(refresh, "rtk"), session["id"], session["expires_at"]),
        )
        return {
            **identity(session, self.settings.server_id),
            "tokenType": "Bearer",
            "accessToken": access,
            "refreshToken": refresh,
            "accessExpiresAt": access_expiry.isoformat(),
            "refreshExpiresAt": session["expires_at"].isoformat(),
        }

    def create(self, *, name, device_name):
        user, device, session = (uuid.uuid4().hex for _ in range(3))
        with self.repo.transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(%s)", (ADMISSION_LOCK,))
            n = db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.sessions WHERE revoked_at IS NULL AND expires_at>now()"
            ).fetchone()["n"]
            if n >= self.settings.max_sessions:
                raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
            db.execute("INSERT INTO ptcg_v2.users(id,name) VALUES(%s,%s)", (user, name))
            db.execute(
                "INSERT INTO ptcg_v2.devices VALUES(%s,%s,%s)",
                (device, user, device_name),
            )
            row = db.execute(
                """INSERT INTO ptcg_v2.sessions(id,user_id,device_id,expires_at)
                VALUES(%s,%s,%s,now()+(%s * interval '1 second')) RETURNING *""",
                (session, user, device, self.settings.refresh_seconds),
            ).fetchone()
            return self._issue(db, row)

    def authenticate_in(self, db, token):
        """Hold a shared session lock through the caller's business commit.

        Refresh/revoke take the exclusive lock. Recheck the access token after
        waiting, so a token replaced while waiting cannot authorize a mutation.
        """
        digest = token_digest(token, "atk")
        ref = db.execute(
            "SELECT session_id FROM ptcg_v2.access_tokens WHERE digest=%s", (digest,)
        ).fetchone()
        if not ref:
            raise ServiceError("UNAUTHORIZED", 401)
        db.execute(
            "SELECT id FROM ptcg_v2.sessions WHERE id=%s FOR SHARE",
            (ref["session_id"],),
        )
        row = db.execute(
            """SELECT s.* FROM ptcg_v2.sessions s JOIN ptcg_v2.access_tokens a ON a.session_id=s.id
            WHERE a.digest=%s AND a.expires_at>clock_timestamp()
            AND s.expires_at>clock_timestamp() AND s.revoked_at IS NULL""",
            (digest,),
        ).fetchone()
        if not row:
            raise ServiceError("UNAUTHORIZED", 401)
        return identity(row, self.settings.server_id)

    def authenticate(self, token):
        with self.repo.transaction() as db:
            return self.authenticate_in(db, token)

    def refresh(self, token):
        digest = token_digest(token, "rtk")
        result = None
        with self.repo.transaction() as db:
            ref = db.execute(
                "SELECT session_id FROM ptcg_v2.refresh_tokens WHERE digest=%s",
                (digest,),
            ).fetchone()
            if not ref:
                raise ServiceError("UNAUTHORIZED", 401)
            session = db.execute(
                "SELECT *, expires_at>now() AS valid FROM ptcg_v2.sessions WHERE id=%s FOR UPDATE",
                (ref["session_id"],),
            ).fetchone()
            row = db.execute(
                "SELECT *, expires_at>now() AS valid FROM ptcg_v2.refresh_tokens WHERE digest=%s",
                (digest,),
            ).fetchone()
            if not session["valid"] or session["revoked_at"] or not row["valid"]:
                raise ServiceError("UNAUTHORIZED", 401)
            if row["used_at"] is not None:
                # Commit revocation before returning 401. Raising inside this transaction
                # would roll back reuse detection and leave the stolen family usable.
                db.execute(
                    "UPDATE ptcg_v2.sessions SET revoked_at=now() WHERE id=%s",
                    (session["id"],),
                )
            else:
                db.execute(
                    "UPDATE ptcg_v2.refresh_tokens SET used_at=now() WHERE digest=%s",
                    (digest,),
                )
                result = self._issue(db, session)
        if result is None:
            raise ServiceError("UNAUTHORIZED", 401)
        return result

    def revoke(self, token):
        digest = token_digest(token, "atk")
        with self.repo.transaction() as db:
            ref = db.execute(
                "SELECT session_id FROM ptcg_v2.access_tokens WHERE digest=%s AND expires_at>now()",
                (digest,),
            ).fetchone()
            if not ref:
                raise ServiceError("UNAUTHORIZED", 401)
            db.execute(
                "SELECT id FROM ptcg_v2.sessions WHERE id=%s FOR UPDATE",
                (ref["session_id"],),
            )
            # Rotation may have won while waiting for this lock.
            if not db.execute(
                "SELECT 1 FROM ptcg_v2.access_tokens WHERE digest=%s AND expires_at>now()",
                (digest,),
            ).fetchone():
                raise ServiceError("UNAUTHORIZED", 401)
            db.execute(
                "UPDATE ptcg_v2.sessions SET revoked_at=COALESCE(revoked_at,now()) WHERE id=%s",
                (ref["session_id"],),
            )
        return {"protocolVersion": "2.0", "revoked": True}

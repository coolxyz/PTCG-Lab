"""Authoritative per-match commits. Candidate rule work never holds SQL locks."""

from hashlib import sha256
import uuid
from psycopg.types.json import Jsonb
from ptcg_engine_provider.values import canonical
from ptcg_protocol.projection import snapshot, core_command, replay_page
from ptcg_protocol.validation import validate, ProtocolViolation
from .auth import Sessions
from .errors import ServiceError

SEATS = ("PLAYER1", "PLAYER2")


class Matches:
    def __init__(self, repo, worker):
        self.repo, self.worker = repo, worker
        self.sessions = Sessions(repo)

    def _authorized(self, db, token, match_id):
        identity = self.sessions.authenticate_in(db, token)
        match = db.execute(
            "SELECT * FROM ptcg_v2.matches WHERE id=%s FOR UPDATE", (match_id,)
        ).fetchone()
        member = db.execute(
            "SELECT * FROM ptcg_v2.match_members WHERE match_id=%s AND user_id=%s",
            (match_id, identity["userId"]),
        ).fetchone()
        if not match or not member:
            raise ServiceError("FORBIDDEN", 403)
        return identity, match, member

    def _current(self, db, match, member):
        frame = db.execute(
            "SELECT snapshot FROM ptcg_v2.match_frames WHERE match_id=%s AND user_id=%s AND match_seq=%s",
            (match["id"], member["user_id"], match["match_seq"]),
        ).fetchone()["snapshot"]
        return validate(
            "MatchSnapshot",
            {
                **frame,
                "controllerEpoch": member["controller_epoch"],
                "streamCursor": member["stream_cursor"],
            },
        )

    def get(self, token, match_id):
        with self.repo.transaction() as db:
            _, match, member = self._authorized(db, token, match_id)
            return self._current(db, match, member)

    def _publish(self, db, member, frame, kind, *, persist_frame):
        cursor = uuid.uuid4().hex
        sequence = member["stream_seq"] + 1
        frame = validate(
            "MatchSnapshot",
            {
                **frame,
                "controllerEpoch": member["controller_epoch"],
                "streamCursor": cursor,
            },
        )
        db.execute(
            "UPDATE ptcg_v2.match_members SET stream_seq=%s,stream_cursor=%s WHERE match_id=%s AND user_id=%s",
            (sequence, cursor, member["match_id"], member["user_id"]),
        )
        if persist_frame:
            db.execute(
                "INSERT INTO ptcg_v2.match_frames VALUES(%s,%s,%s,%s,%s)",
                (
                    member["match_id"],
                    member["user_id"],
                    frame["matchSeq"],
                    cursor,
                    Jsonb(frame),
                ),
            )
        db.execute(
            "INSERT INTO ptcg_v2.match_outbox VALUES(%s,%s,%s,%s,%s,%s)",
            (
                member["match_id"],
                member["user_id"],
                sequence,
                cursor,
                kind,
                Jsonb(frame),
            ),
        )
        db.execute(
            "DELETE FROM ptcg_v2.match_outbox WHERE match_id=%s AND user_id=%s AND stream_seq<=%s",
            (
                member["match_id"],
                member["user_id"],
                sequence - self.repo.settings.outbox_retention,
            ),
        )
        db.stream_changed = True
        return frame

    def _frames(self, db, match, candidate):
        members = db.execute(
            "SELECT * FROM ptcg_v2.match_members WHERE match_id=%s ORDER BY seat",
            (match["id"],),
        ).fetchall()
        frames = {}
        for member in members:
            seat = member["seat"]
            frame = snapshot(
                candidate["views"][seat],
                match_id=match["id"],
                rule_release_id=match["rule_release_id"],
                viewer_seat=seat,
                match_seq=match["match_seq"],
                controller_epoch=member["controller_epoch"],
                stream_cursor="pending",
                visual_effects=candidate["effects"][seat],
                result=candidate["results"][seat],
            )
            frame["events"] = [
                {**event, "seq": match["match_seq"]}
                for event in candidate.get("events", {}).get(seat, [])
            ]
            frames[seat] = self._publish(
                db,
                member,
                frame,
                "snapshot" if match["match_seq"] == 0 else "update",
                persist_frame=True,
            )
        return frames

    def promote_one(self):
        # Also promotes P4-B initial records without drawing a new initial hand.
        with self.repo.transaction() as db:
            job = db.execute(
                """SELECT i.* FROM ptcg_v2.initializations i LEFT JOIN ptcg_v2.matches m ON m.id=i.match_id
                WHERE i.status='initialized' AND m.id IS NULL AND i.rule_release_id=%s ORDER BY i.created_at LIMIT 1""",
                (self.repo.settings.approved_rule_release_id,),
            ).fetchone()
        if not job or not self.worker.available:
            return False
        candidate = self.worker.project(job["recovery"])
        self._candidate(candidate, job["rule_release_id"], expected_version=0)
        with self.repo.transaction() as db:
            match = db.execute(
                """INSERT INTO ptcg_v2.matches(id,room_id,rule_release_id,state_version,status,recovery)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING *""",
                (
                    job["match_id"],
                    job["room_id"],
                    job["rule_release_id"],
                    0,
                    "finished" if candidate["views"]["PLAYER1"]["done"] else "playing",
                    Jsonb(candidate["recovery"]),
                ),
            ).fetchone()
            if not match:
                return False
            for seat, user in zip(SEATS, (job["player1"], job["player2"])):
                db.execute(
                    "INSERT INTO ptcg_v2.match_members(match_id,user_id,seat) VALUES(%s,%s,%s)",
                    (match["id"], user, seat),
                )
            self._frames(db, match, candidate)
            if match["status"] != "playing":
                self._release_room(db, match)
        return True

    def _candidate(self, candidate, release, expected_version):
        views, record = candidate["views"], candidate["recovery"]
        if (
            set(views) != set(SEATS)
            or record.get("implementation") != release
            or record.get("schema") != "core-recovery-v2"
            or any(views[s]["stateVersion"] != expected_version for s in SEATS)
            or views["PLAYER1"]["done"] != views["PLAYER2"]["done"]
            or views["PLAYER1"]["winner"] != views["PLAYER2"]["winner"]
        ):
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503)

    def _digest(self, operation, body, controller):
        return sha256(
            canonical({"operation": operation, "body": body, "controller": controller})
        ).hexdigest()

    def _old(self, db, match, member, key, digest, *, control=False):
        # Table names are internal constants, never client input.
        table, field = (
            ("control_receipts", "request_id")
            if control
            else ("match_receipts", "command_id")
        )
        old = db.execute(
            f"SELECT digest,receipt FROM ptcg_v2.{table} WHERE match_id=%s AND user_id=%s AND {field}=%s",
            (match["id"], member["user_id"], key),
        ).fetchone()
        if old:
            if old["digest"] != digest:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
            return old["receipt"]
        return None

    def control(self, token, match_id, body):
        digest = self._digest("control", body, body["controllerId"])
        with self.repo.transaction() as db:
            identity, match, member = self._authorized(db, token, match_id)
            old = self._old(db, match, member, body["requestId"], digest, control=True)
            if old is not None:
                return old
            if match["status"] != "playing":
                raise ServiceError("MATCH_VERSION_CONFLICT", 409)
            if member["controller_epoch"] != body["expectedControllerEpoch"]:
                raise ServiceError("CONTROL_LOST", 409)
            n = db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.control_receipts WHERE match_id=%s AND user_id=%s",
                (match_id, member["user_id"]),
            ).fetchone()["n"]
            if n >= self.repo.settings.max_match_commands:
                raise ServiceError("CAPACITY_EXCEEDED", 503)
            member = db.execute(
                """UPDATE ptcg_v2.match_members SET controller_epoch=controller_epoch+1,controller_id=%s,
                controller_session=%s WHERE match_id=%s AND user_id=%s RETURNING *""",
                (
                    body["controllerId"],
                    identity["sessionId"],
                    match_id,
                    member["user_id"],
                ),
            ).fetchone()
            current = self._current(db, match, member)
            result = self._publish(db, member, current, "control", persist_frame=False)
            db.execute(
                "INSERT INTO ptcg_v2.control_receipts VALUES(%s,%s,%s,%s,%s)",
                (match_id, member["user_id"], body["requestId"], digest, Jsonb(result)),
            )
            return result

    def _preconditions(self, identity, match, member, body, controller):
        if (
            member["controller_session"] != identity["sessionId"]
            or member["controller_id"] != controller
            or member["controller_epoch"] != body["controllerEpoch"]
        ):
            raise ServiceError("CONTROL_LOST", 409)
        if (
            match["status"] != "playing"
            or match["match_seq"] != body["expectedMatchSeq"]
        ):
            raise ServiceError("MATCH_VERSION_CONFLICT", 409)
        if match["state_version"] != body["expectedStateVersion"]:
            raise ServiceError("STALE_DECISION", 409)

    def command_admission(self, token, match_id, body, controller):
        # No recovery/frame read or match lock before queueing. Every caller,
        # including merged retries, must authorize and check its durable receipt.
        digest = self._digest("command", body, controller)
        with self.repo.transaction() as db:
            identity = self.sessions.authenticate_in(db, token)
            member = db.execute(
                "SELECT user_id FROM ptcg_v2.match_members WHERE match_id=%s AND user_id=%s",
                (match_id, identity["userId"]),
            ).fetchone()
            if not member:
                raise ServiceError("FORBIDDEN", 403)
            old = self._old(db, {"id": match_id}, member, body["commandId"], digest)
            return identity["userId"], digest, old

    def command(self, token, match_id, body, controller):
        digest = self._digest("command", body, controller)
        with self.repo.transaction() as db:
            identity, match, member = self._authorized(db, token, match_id)
            old = self._old(db, match, member, body["commandId"], digest)
            if old is not None:
                return old
            self._preconditions(identity, match, member, body, controller)
            if match["match_seq"] >= self.repo.settings.max_match_commands:
                raise ServiceError("CAPACITY_EXCEEDED", 503)
            if match["rule_release_id"] != self.repo.settings.approved_rule_release_id:
                raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503)
            try:
                command = core_command(
                    body,
                    trusted_view=self._current(db, match, member),
                    authenticated_seat=member["seat"],
                )
            except ProtocolViolation as error:
                raise ServiceError(error.code, 409) from None
        command["commandId"] = (
            member["seat"] + ":" + sha256(body["commandId"].encode()).hexdigest()
        )
        candidate = self.worker.command(match["recovery"], member["seat"], command)
        self._candidate(candidate, match["rule_release_id"], match["state_version"] + 1)
        with self.repo.transaction() as db:
            identity, current, own = self._authorized(db, token, match_id)
            old = self._old(db, current, own, body["commandId"], digest)
            if old is not None:
                return old
            # Revocation, takeover or another commit during computation fences this candidate.
            self._preconditions(identity, current, own, body, controller)
            current = db.execute(
                """UPDATE ptcg_v2.matches SET match_seq=match_seq+1,state_version=state_version+1,
                recovery=%s,status=%s WHERE id=%s RETURNING *""",
                (
                    Jsonb(candidate["recovery"]),
                    "finished" if candidate["views"]["PLAYER1"]["done"] else "playing",
                    match_id,
                ),
            ).fetchone()
            frames = self._frames(db, current, candidate)
            receipt = self._remember(
                db, current, own, body["commandId"], digest, frames[own["seat"]]
            )
            if current["status"] != "playing":
                self._release_room(db, current)
            return receipt

    def _remember(self, db, match, own, command_id, digest, frame):
        receipt = {
            "protocolVersion": "2.0",
            "matchId": match["id"],
            "commandId": command_id,
            "accepted": True,
            "matchSeq": match["match_seq"],
            "stateVersion": match["state_version"],
            "streamCursor": frame["streamCursor"],
        }
        db.execute(
            "INSERT INTO ptcg_v2.match_receipts VALUES(%s,%s,%s,%s,%s)",
            (match["id"], own["user_id"], command_id, digest, Jsonb(receipt)),
        )
        return receipt

    def _release_room(self, db, match):
        db.execute(
            "UPDATE ptcg_v2.rooms SET status='closed',version=version+1 WHERE id=%s",
            (match["room_id"],),
        )

    def resign(self, token, match_id, body, controller):
        digest = self._digest("resign", body, controller)
        with self.repo.transaction() as db:
            identity, match, own = self._authorized(db, token, match_id)
            old = self._old(db, match, own, body["commandId"], digest)
            if old is not None:
                return old
            self._preconditions(identity, match, own, body, controller)
            match = db.execute(
                "UPDATE ptcg_v2.matches SET match_seq=match_seq+1,status='finished' WHERE id=%s RETURNING *",
                (match_id,),
            ).fetchone()
            frames = self._terminal_frames(
                db, match, "resigned", SEATS[1] if own["seat"] == SEATS[0] else SEATS[0]
            )
            self._release_room(db, match)
            return self._remember(
                db, match, own, body["commandId"], digest, frames[own["seat"]]
            )

    def _terminal_frames(self, db, match, reason, winner):
        result = {}
        for member in db.execute(
            "SELECT * FROM ptcg_v2.match_members WHERE match_id=%s ORDER BY seat",
            (match["id"],),
        ).fetchall():
            previous = db.execute(
                "SELECT snapshot FROM ptcg_v2.match_frames WHERE match_id=%s AND user_id=%s ORDER BY match_seq DESC LIMIT 1",
                (match["id"], member["user_id"]),
            ).fetchone()["snapshot"]
            frame = {
                **previous,
                "matchSeq": match["match_seq"],
                "status": match["status"],
                "done": True,
                "winner": winner,
                "decision": None,
                "phase": reason,
                "events": [
                    {
                        "seq": match["match_seq"],
                        "actor": None,
                        "kind": "result",
                        "text": "对局因认输结束"
                        if reason == "resigned"
                        else "对局因技术原因终止",
                    }
                ],
                "visualEffects": [],
                "result": {"reason": reason, "conditions": [reason]},
            }
            result[member["seat"]] = self._publish(
                db, member, frame, "update", persist_frame=True
            )
        return result

    def technical_abort(self, match_id, expected_seq):
        """Operator/internal repair only; never exposed as a player endpoint."""
        with self.repo.transaction() as db:
            match = db.execute(
                "SELECT * FROM ptcg_v2.matches WHERE id=%s FOR UPDATE", (match_id,)
            ).fetchone()
            if (
                not match
                or match["match_seq"] != expected_seq
                or match["status"] != "playing"
            ):
                return False
            match = db.execute(
                "UPDATE ptcg_v2.matches SET match_seq=match_seq+1,status='aborted' WHERE id=%s RETURNING *",
                (match_id,),
            ).fetchone()
            self._terminal_frames(db, match, "technical_abort", None)
            self._release_room(db, match)
            return True

    def receipt(self, token, match_id, command_id):
        with self.repo.transaction() as db:
            _, match, own = self._authorized(db, token, match_id)
            row = db.execute(
                "SELECT receipt FROM ptcg_v2.match_receipts WHERE match_id=%s AND user_id=%s AND command_id=%s",
                (match_id, own["user_id"], command_id),
            ).fetchone()
            if not row:
                raise ServiceError("INVALID_PAYLOAD", 404)
            return row["receipt"]

    def frames(self, token, match_id, cursor=None):
        with self.repo.transaction() as db:
            _, match, own = self._authorized(db, token, match_id)
            seq = -1
            if cursor:
                row = db.execute(
                    "SELECT match_seq FROM ptcg_v2.match_frames WHERE match_id=%s AND user_id=%s AND cursor=%s",
                    (match_id, own["user_id"], cursor),
                ).fetchone()
                if not row:
                    raise ServiceError("RESYNC_REQUIRED", 409)
                seq = row["match_seq"]
            rows = db.execute(
                "SELECT snapshot,cursor FROM ptcg_v2.match_frames WHERE match_id=%s AND user_id=%s AND match_seq>%s ORDER BY match_seq LIMIT 4",
                (match_id, own["user_id"], seq),
            ).fetchall()
            if rows:
                return replay_page(
                    [r["snapshot"] for r in rows[:3]],
                    next_cursor=rows[2]["cursor"] if len(rows) > 3 else None,
                )
            return validate(
                "ReplayPage",
                {
                    "protocolVersion": "2.0",
                    "replaySchemaVersion": "visible-replay-v2",
                    "matchId": match_id,
                    "ruleReleaseId": match["rule_release_id"],
                    "viewerSeat": own["seat"],
                    "frames": [],
                    "nextCursor": None,
                },
            )

    def stream(self, token, match_id, cursor=None):
        with self.repo.transaction() as db:
            _, match, own = self._authorized(db, token, match_id)
            if cursor is None:
                return [{"kind": "snapshot", "snapshot": self._current(db, match, own)}]
            row = db.execute(
                "SELECT stream_seq FROM ptcg_v2.match_outbox WHERE match_id=%s AND user_id=%s AND cursor=%s",
                (match_id, own["user_id"], cursor),
            ).fetchone()
            if not row:
                raise ServiceError("RESYNC_REQUIRED", 409)
            return db.execute(
                "SELECT kind,snapshot FROM ptcg_v2.match_outbox WHERE match_id=%s AND user_id=%s AND stream_seq>%s ORDER BY stream_seq LIMIT 8",
                (match_id, own["user_id"], row["stream_seq"]),
            ).fetchall()

    def stream_heads(self, subscriptions):
        """One bounded metadata query; never load snapshots or authoritative state.

        Heads are per recipient, not a global sequence allocated before commit.
        Every scan rechecks token/session validity. This is only a wakeup hint;
        stream() still authorizes and validates the cursor before reading frames.
        """
        if not subscriptions:
            return []
        if len(subscriptions) > self.repo.settings.max_streams:
            raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
        with self.repo.transaction() as db:
            return db.execute(
                """SELECT q.key,
                CASE WHEN s.id IS NULL THEN 'UNAUTHORIZED'
                     WHEN m.user_id IS NULL THEN 'FORBIDDEN' END AS error,
                m.stream_cursor AS cursor
                FROM jsonb_to_recordset(%s) AS q(key text, digest text, match_id text)
                LEFT JOIN ptcg_v2.access_tokens a ON a.digest=q.digest
                    AND a.expires_at>clock_timestamp()
                LEFT JOIN ptcg_v2.sessions s ON s.id=a.session_id
                    AND s.expires_at>clock_timestamp() AND s.revoked_at IS NULL
                LEFT JOIN ptcg_v2.match_members m ON m.match_id=q.match_id
                    AND m.user_id=s.user_id""",
                (Jsonb(subscriptions),),
            ).fetchall()

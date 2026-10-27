"""Durable lobby mutations; no rule execution inside a database transaction."""

from hashlib import sha256
import secrets
import uuid
from psycopg.types.json import Jsonb
from ptcg_engine_provider.values import canonical
from .auth import Sessions
from .errors import ServiceError

ROOM_ADMISSION_LOCK = 74040200


def uid():
    return uuid.uuid4().hex


class Lobby:
    def __init__(self, repository, worker):
        self.repo, self.worker = repository, worker
        self.sessions = Sessions(repository)

    def _authorize(self, db, token):
        user = self.sessions.authenticate_in(db, token)["userId"]
        # Serialize this user's request IDs across devices and endpoints.
        db.execute("SELECT id FROM ptcg_v2.users WHERE id=%s FOR UPDATE", (user,))
        return user

    def _receipt(self, db, user, action, body):
        digest = sha256(canonical({"action": action, "body": body})).hexdigest()
        old = db.execute(
            "SELECT * FROM ptcg_v2.lobby_receipts WHERE user_id=%s AND request_id=%s",
            (user, body["requestId"]),
        ).fetchone()
        if old:
            if old["digest"] != digest:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
            return digest, old["response"]
        count = db.execute(
            "SELECT count(*) AS n FROM ptcg_v2.lobby_receipts WHERE user_id=%s", (user,)
        ).fetchone()["n"]
        if count >= self.repo.settings.max_lobby_receipts:
            raise ServiceError("CAPACITY_EXCEEDED", 503)
        return digest, None

    def mutate(self, token, action, body, operation):
        with self.repo.transaction() as db:
            user = self._authorize(db, token)
            digest, old = self._receipt(db, user, action, body)
            if old is not None:
                return old
            result = operation(db, user)
            db.execute(
                "INSERT INTO ptcg_v2.lobby_receipts(user_id,request_id,digest,response) VALUES(%s,%s,%s,%s)",
                (user, body["requestId"], digest, Jsonb(result)),
            )
            return result

    def require_worker(self, release):
        if (
            not self.worker.available
            or release != self.repo.settings.approved_rule_release_id
        ):
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503, retryable=True)

    def deck_admission(self, token, body):
        with self.repo.transaction() as db:
            user = self._authorize(db, token)
            digest, old = self._receipt(db, user, "deck", body)
            return user, digest, old

    def submit_deck(self, token, body):
        # Completed retries work even if the rule process is temporarily unavailable.
        with self.repo.transaction() as db:
            user = self._authorize(db, token)
            _, old = self._receipt(db, user, "deck", body)
            if old is not None:
                return old
        self.require_worker(body["ruleReleaseId"])
        validated = self.worker.validate(body["entries"])

        def save(db, user):
            count = db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.deck_revisions WHERE user_id=%s",
                (user,),
            ).fetchone()["n"]
            if count >= self.repo.settings.max_decks_per_user:
                raise ServiceError("CAPACITY_EXCEEDED", 503)
            revision = uid()
            db.execute(
                "INSERT INTO ptcg_v2.deck_revisions(id,user_id,rule_release_id,as_of,entries) VALUES(%s,%s,%s,%s,%s)",
                (
                    revision,
                    user,
                    body["ruleReleaseId"],
                    validated["asOf"],
                    Jsonb(validated["entries"]),
                ),
            )
            return {
                "protocolVersion": "2.0",
                "deckRevision": revision,
                "ruleReleaseId": body["ruleReleaseId"],
                "formatAsOf": validated["asOf"],
            }

        return self.mutate(token, "deck", body, save)

    def _deck(self, db, user, revision, release, as_of=None):
        deck = db.execute(
            "SELECT * FROM ptcg_v2.deck_revisions WHERE id=%s AND user_id=%s",
            (revision, user),
        ).fetchone()
        if not deck:
            raise ServiceError("FORBIDDEN", 403)
        if deck["rule_release_id"] != release or (as_of and deck["as_of"] != as_of):
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 409)
        return deck

    def _room(self, db, room_id, user):
        room = db.execute(
            "SELECT * FROM ptcg_v2.rooms WHERE id=%s FOR UPDATE", (room_id,)
        ).fetchone()
        if (
            not room
            or not db.execute(
                "SELECT 1 FROM ptcg_v2.room_members WHERE room_id=%s AND user_id=%s AND left_at IS NULL",
                (room_id, user),
            ).fetchone()
        ):
            raise ServiceError("FORBIDDEN", 403)
        return room

    def _view(self, db, room, user):
        members = db.execute(
            "SELECT user_id,seat,ready,deck_revision FROM ptcg_v2.room_members WHERE room_id=%s AND left_at IS NULL ORDER BY seat",
            (room["id"],),
        ).fetchall()
        own = next(m for m in members if m["user_id"] == user)
        # No opponent revision, card list, seed, private record or internal job data.
        return {
            "protocolVersion": "2.0",
            "roomId": room["id"],
            "roomVersion": room["version"],
            "inviteCode": room["invite_code"],
            "ruleReleaseId": room["rule_release_id"],
            "status": room["status"],
            "expiresAt": room["expires_at"].isoformat(),
            "viewerSeat": own["seat"],
            "deckRevision": own["deck_revision"],
            "members": [{"seat": m["seat"], "ready": m["ready"]} for m in members],
            "matchId": room["match_id"],
        }

    def _waiting(self, db, room, body=None):
        alive = db.execute(
            "SELECT %s::timestamptz > clock_timestamp() AS alive", (room["expires_at"],)
        ).fetchone()["alive"]
        if room["status"] != "waiting" or not alive:
            raise ServiceError("MATCH_VERSION_CONFLICT", 409)
        if body and body["expectedRoomVersion"] != room["version"]:
            raise ServiceError("MATCH_VERSION_CONFLICT", 409)

    def _not_in_room(self, db, user):
        if db.execute(
            """SELECT 1 FROM ptcg_v2.room_members m JOIN ptcg_v2.rooms r ON r.id=m.room_id
                      WHERE m.user_id=%s AND m.left_at IS NULL AND
                      ((r.status='waiting' AND r.expires_at>now()) OR r.status IN ('initializing','initialized'))""",
            (user,),
        ).fetchone():
            raise ServiceError("MATCH_VERSION_CONFLICT", 409)

    def create(self, token, body):
        def operation(db, user):
            self.require_worker(body["ruleReleaseId"])
            self._not_in_room(db, user)
            deck = self._deck(db, user, body["deckRevision"], body["ruleReleaseId"])
            db.execute("SELECT pg_advisory_xact_lock(%s)", (ROOM_ADMISSION_LOCK,))
            count = db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.rooms WHERE (status='waiting' AND expires_at>now()) OR status IN ('initializing','initialized')"
            ).fetchone()["n"]
            if count >= self.repo.settings.max_rooms:
                raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
            # Retry a collision without aborting the transaction or overwriting a room.
            room = None
            for _ in range(5):
                code = "".join(
                    secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
                    for _ in range(10)
                )
                room = db.execute(
                    """INSERT INTO ptcg_v2.rooms(id,invite_code,rule_release_id,as_of,status,expires_at)
                    VALUES(%s,%s,%s,%s,'waiting',now()+(%s*interval '1 second'))
                    ON CONFLICT(invite_code) DO NOTHING RETURNING *""",
                    (
                        uid(),
                        code,
                        deck["rule_release_id"],
                        deck["as_of"],
                        self.repo.settings.room_seconds,
                    ),
                ).fetchone()
                if room:
                    break
            if not room:
                raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
            db.execute(
                "INSERT INTO ptcg_v2.room_members(room_id,user_id,seat,deck_revision) VALUES(%s,%s,'PLAYER1',%s)",
                (room["id"], user, deck["id"]),
            )
            return self._view(db, room, user)

        return self.mutate(token, "create", body, operation)

    def join(self, token, body):
        def operation(db, user):
            room = db.execute(
                "SELECT * FROM ptcg_v2.rooms WHERE invite_code=%s FOR UPDATE",
                (body["inviteCode"],),
            ).fetchone()
            if not room:
                raise ServiceError("FORBIDDEN", 403)
            self._waiting(db, room)
            self.require_worker(room["rule_release_id"])
            self._not_in_room(db, user)
            deck = self._deck(
                db, user, body["deckRevision"], room["rule_release_id"], room["as_of"]
            )
            if (
                db.execute(
                    "SELECT count(*) AS n FROM ptcg_v2.room_members WHERE room_id=%s",
                    (room["id"],),
                ).fetchone()["n"]
                != 1
            ):
                raise ServiceError("FORBIDDEN", 403)
            db.execute(
                "INSERT INTO ptcg_v2.room_members(room_id,user_id,seat,deck_revision) VALUES(%s,%s,'PLAYER2',%s)",
                (room["id"], user, deck["id"]),
            )
            room = db.execute(
                "UPDATE ptcg_v2.rooms SET version=version+1 WHERE id=%s RETURNING *",
                (room["id"],),
            ).fetchone()
            return self._view(db, room, user)

        return self.mutate(token, "join", body, operation)

    def get(self, token, room_id):
        with self.repo.transaction() as db:
            user = self.sessions.authenticate_in(db, token)["userId"]
            room = self._room(db, room_id, user)
            if room["status"] == "waiting":
                expired = db.execute(
                    "UPDATE ptcg_v2.rooms SET status='closed',version=version+1 WHERE id=%s AND expires_at<=now() RETURNING *",
                    (room_id,),
                ).fetchone()
                room = expired or room
            return self._view(db, room, user)

    def change(self, token, room_id, action, body):
        def operation(db, user):
            room = self._room(db, room_id, user)
            self._waiting(db, room, body)
            if action == "leave":
                db.execute(
                    "UPDATE ptcg_v2.room_members SET left_at=now(),ready=false WHERE room_id=%s AND user_id=%s",
                    (room_id, user),
                )
                room = db.execute(
                    "UPDATE ptcg_v2.rooms SET status='closed',version=version+1 WHERE id=%s RETURNING *",
                    (room_id,),
                ).fetchone()
                return {
                    "protocolVersion": "2.0",
                    "roomId": room_id,
                    "roomVersion": room["version"],
                    "left": True,
                }
            if action == "deck":
                self.require_worker(room["rule_release_id"])
                self._deck(
                    db,
                    user,
                    body["deckRevision"],
                    room["rule_release_id"],
                    room["as_of"],
                )
                db.execute(
                    "UPDATE ptcg_v2.room_members SET deck_revision=%s WHERE room_id=%s AND user_id=%s",
                    (body["deckRevision"], room_id, user),
                )
                db.execute(
                    "UPDATE ptcg_v2.room_members SET ready=false WHERE room_id=%s",
                    (room_id,),
                )
            elif action == "ready":
                if body["ready"]:
                    self.require_worker(room["rule_release_id"])
                db.execute(
                    "UPDATE ptcg_v2.room_members SET ready=%s WHERE room_id=%s AND user_id=%s",
                    (body["ready"], room_id, user),
                )
            else:
                raise ServiceError("INVALID_PAYLOAD", 400)
            members = db.execute(
                "SELECT * FROM ptcg_v2.room_members WHERE room_id=%s ORDER BY seat",
                (room_id,),
            ).fetchall()
            if len(members) == 2 and all(m["ready"] for m in members):
                decks = [
                    self._deck(
                        db,
                        m["user_id"],
                        m["deck_revision"],
                        room["rule_release_id"],
                        room["as_of"],
                    )
                    for m in members
                ]
                match_id, seed = uid(), secrets.randbelow(9007199254740992)
                db.execute(
                    """INSERT INTO ptcg_v2.initializations(match_id,room_id,seed,rule_release_id,as_of,player1,player2,revision1,revision2,deck1,deck2)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        match_id,
                        room_id,
                        seed,
                        room["rule_release_id"],
                        room["as_of"],
                        members[0]["user_id"],
                        members[1]["user_id"],
                        decks[0]["id"],
                        decks[1]["id"],
                        Jsonb(decks[0]["entries"]),
                        Jsonb(decks[1]["entries"]),
                    ),
                )
                db.execute(
                    "UPDATE ptcg_v2.rooms SET status='initializing',match_id=%s WHERE id=%s",
                    (match_id, room_id),
                )
            room = db.execute(
                "UPDATE ptcg_v2.rooms SET version=version+1 WHERE id=%s RETURNING *",
                (room_id,),
            ).fetchone()
            return self._view(db, room, user)

        return self.mutate(token, action + ":" + room_id, body, operation)

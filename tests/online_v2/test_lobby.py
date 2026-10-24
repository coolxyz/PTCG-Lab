"""Real PostgreSQL lobby races with an explicitly synthetic rule executor."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import uuid
import pytest
from fastapi.testclient import TestClient
from ptcg_online_server.api import create_app
from ptcg_online_server.auth import Sessions
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.initialization import Initializer
from ptcg_online_server.lobby import Lobby
from ptcg_online_server.repository import Repository

RELEASE = "rules-synthetic-lobby"
ENTRIES = [
    {"printingId": "synthetic-basic", "quantity": 4},
    {"printingId": "synthetic-energy", "quantity": 56},
]


class SyntheticWorker:
    available = True

    def probe(self):
        return self.available

    def validate(self, entries):
        if entries != ENTRIES:
            raise ServiceError("INVALID_PAYLOAD", 422)
        return {"entries": entries, "asOf": "2026-10-01"}

    def initialize(self, job):
        return {
            "schema": "synthetic-only",
            "seed": job["seed"],
            "private": "never-on-wire",
        }


def body(**values):
    return {"protocolVersion": "2.0", "requestId": uuid.uuid4().hex, **values}


@pytest.fixture
def lobby(repo):
    return Lobby(
        Repository(replace(repo.settings, approved_rule_release_id=RELEASE)),
        SyntheticWorker(),
    )


def player(lobby):
    tokens = Sessions(lobby.repo).create(name="synthetic", device_name="test")
    token = tokens["accessToken"]
    submitted = body(ruleReleaseId=RELEASE, entries=ENTRIES)
    deck = lobby.submit_deck(token, submitted)
    return token, deck["deckRevision"], tokens


def pair(lobby):
    a, da, ta = player(lobby)
    b, db, tb = player(lobby)
    created = body(ruleReleaseId=RELEASE, deckRevision=da)
    room = lobby.create(a, created)
    joined = lobby.join(b, body(inviteCode=room["inviteCode"], deckRevision=db))
    return a, b, joined, created


def prepare(lobby):
    a, b, room, _ = pair(lobby)
    room = lobby.change(
        a,
        room["roomId"],
        "ready",
        body(expectedRoomVersion=room["roomVersion"], ready=True),
    )
    ready = body(expectedRoomVersion=room["roomVersion"], ready=True)
    room = lobby.change(b, room["roomId"], "ready", ready)
    return a, b, room, ready


def test_deck_revision_idempotency_ownership_and_revalidation(lobby):
    a, da, _ = player(lobby)
    b, _, _ = player(lobby)
    request = body(ruleReleaseId=RELEASE, entries=ENTRIES)
    first = lobby.submit_deck(a, request)
    lobby.worker.available = False
    assert lobby.submit_deck(a, request) == first
    with pytest.raises(ServiceError, match="IDEMPOTENCY_CONFLICT"):
        lobby.submit_deck(a, {**request, "name": "changed"})
    lobby.worker.available = True
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        lobby.create(b, body(ruleReleaseId=RELEASE, deckRevision=da))
    with pytest.raises(ServiceError, match="INVALID_PAYLOAD"):
        lobby.submit_deck(
            a,
            body(
                ruleReleaseId=RELEASE,
                entries=[{"printingId": "forged", "quantity": 60}],
            ),
        )
    with lobby.repo.db() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.deck_revisions").fetchone()[
                "n"
            ]
            == 3
        )


def test_private_room_projection_third_user_and_request_scope(lobby):
    a, b, room, create = pair(lobby)
    c, dc, _ = player(lobby)
    own = lobby.get(a, room["roomId"])
    assert own["viewerSeat"] == "PLAYER1" and room["viewerSeat"] == "PLAYER2"
    text = json.dumps(own)
    assert all(
        key not in text
        for key in (
            "entries",
            "seed",
            "recovery",
            "userId",
            "lease_token",
            room["deckRevision"],
        )
    )
    for operation in (
        lambda: lobby.get(c, room["roomId"]),
        lambda: lobby.join(c, body(inviteCode=room["inviteCode"], deckRevision=dc)),
        lambda: lobby.change(
            c,
            room["roomId"],
            "ready",
            body(expectedRoomVersion=room["roomVersion"], ready=True),
        ),
    ):
        with pytest.raises(ServiceError, match="FORBIDDEN"):
            operation()
    # Receipt is an acknowledgement of the original version, not the latest room.
    assert lobby.create(a, create)["roomVersion"] == 1
    with pytest.raises(ServiceError, match="IDEMPOTENCY_CONFLICT"):
        lobby.change(
            a,
            room["roomId"],
            "leave",
            {**body(expectedRoomVersion=2), "requestId": create["requestId"]},
        )


def test_same_ready_race_has_one_job_and_one_stable_seed(lobby):
    a, b, room, _ = pair(lobby)
    room = lobby.change(
        a, room["roomId"], "ready", body(expectedRoomVersion=2, ready=True)
    )
    request = body(expectedRoomVersion=room["roomVersion"], ready=True)
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        return lobby.change(b, room["roomId"], "ready", request)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        results = [f.result() for f in futures]
    assert results[0] == results[1] and results[0]["status"] == "initializing"
    with lobby.repo.db() as db:
        jobs = db.execute("SELECT * FROM ptcg_v2.initializations").fetchall()
    assert len(jobs) == 1 and jobs[0]["match_id"] == results[0]["matchId"]
    restarted = Lobby(lobby.repo, SyntheticWorker())
    assert restarted.change(b, room["roomId"], "ready", request) == results[0]
    with lobby.repo.db() as db:
        assert (
            db.execute("SELECT seed FROM ptcg_v2.initializations").fetchone()["seed"]
            == jobs[0]["seed"]
        )


def test_different_ready_requests_conflict_then_retry_new_version(lobby):
    a, b, room, _ = pair(lobby)

    def ready(token):
        try:
            return lobby.change(
                token, room["roomId"], "ready", body(expectedRoomVersion=2, ready=True)
            )
        except ServiceError as e:
            return e.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(ready, [a, b]))
    assert sum(r == "MATCH_VERSION_CONFLICT" for r in results) == 1
    loser = a if isinstance(results[0], str) else b
    current = lobby.get(loser, room["roomId"])
    assert (
        lobby.change(
            loser,
            room["roomId"],
            "ready",
            body(expectedRoomVersion=current["roomVersion"], ready=True),
        )["status"]
        == "initializing"
    )
    with lobby.repo.db() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.initializations").fetchone()[
                "n"
            ]
            == 1
        )


def test_swap_cancels_both_readiness_leave_closes_and_ttl_blocks_join(lobby):
    a, b, room, _ = pair(lobby)
    room = lobby.change(
        a, room["roomId"], "ready", body(expectedRoomVersion=2, ready=True)
    )
    room = lobby.get(b, room["roomId"])
    room = lobby.change(
        b,
        room["roomId"],
        "deck",
        body(expectedRoomVersion=3, deckRevision=room["deckRevision"]),
    )
    assert all(not m["ready"] for m in room["members"])
    leave = body(expectedRoomVersion=4)
    assert lobby.change(b, room["roomId"], "leave", leave)["left"]
    assert lobby.change(b, room["roomId"], "leave", leave)["left"]
    assert lobby.get(a, room["roomId"])["status"] == "closed"
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        lobby.get(b, room["roomId"])
    a2, da2, _ = player(lobby)
    room2 = lobby.create(a2, body(ruleReleaseId=RELEASE, deckRevision=da2))
    with lobby.repo.db() as db:
        db.execute(
            "UPDATE ptcg_v2.rooms SET expires_at=now()-interval '1 second' WHERE id=%s",
            (room2["roomId"],),
        )
    with pytest.raises(ServiceError, match="MATCH_VERSION_CONFLICT"):
        lobby.join(
            b, body(inviteCode=room2["inviteCode"], deckRevision=room["deckRevision"])
        )
    assert lobby.get(a2, room2["roomId"])["status"] == "closed"


def test_claim_fencing_restart_and_recovery_commit(lobby):
    a, b, room, request = prepare(lobby)
    one, two = Initializer(lobby.repo, lobby.worker), Initializer(
        lobby.repo, SyntheticWorker()
    )
    old = one.claim()
    assert two.claim() is None
    with lobby.repo.db() as db:
        db.execute(
            "UPDATE ptcg_v2.initializations SET lease_until=now()-interval '1 second'"
        )
    new = two.claim()
    assert old["seed"] == new["seed"] and old["match_id"] == new["match_id"]
    assert not one.finish(old, {"private": "stale"})
    assert two.finish(new, two.worker.initialize(new))
    assert one.claim() is None
    assert lobby.get(a, room["roomId"])["status"] == "initialized"
    assert "never-on-wire" not in json.dumps(lobby.get(b, room["roomId"]))
    with pytest.raises(ServiceError, match="MATCH_VERSION_CONFLICT"):
        lobby.change(a, room["roomId"], "leave", body(expectedRoomVersion=5))


def test_initializer_failure_is_bounded_technical_abort_without_reroll(lobby):
    a, b, room, _ = prepare(lobby)
    initializer = Initializer(lobby.repo, lobby.worker)
    seen = []
    for _ in range(3):
        job = initializer.claim()
        seen.append((job["seed"], job["match_id"], job["deck1"], job["deck2"]))
        assert not initializer.finish(job, None)
        with lobby.repo.db() as db:
            db.execute(
                "UPDATE ptcg_v2.initializations SET retry_at=now()-interval '1 second'"
            )
    assert seen[0] == seen[1] == seen[2]
    assert initializer.claim() is None
    assert lobby.get(a, room["roomId"])["status"] == "technical_abort"
    with lobby.repo.db() as db:
        assert (
            db.execute("SELECT recovery FROM ptcg_v2.initializations").fetchone()[
                "recovery"
            ]
            is None
        )


def test_room_and_revision_admission_are_bounded(lobby):
    a, da, _ = player(lobby)
    b, db, _ = player(lobby)
    limited = Lobby(
        Repository(replace(lobby.repo.settings, max_rooms=1, max_decks_per_user=1)),
        lobby.worker,
    )
    limited.create(a, body(ruleReleaseId=RELEASE, deckRevision=da))
    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
        limited.create(b, body(ruleReleaseId=RELEASE, deckRevision=db))
    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
        limited.submit_deck(b, body(ruleReleaseId=RELEASE, entries=ENTRIES))


def test_http_lobby_uses_bearer_membership_and_strict_contracts(lobby):
    with TestClient(
        create_app(lobby.repo.settings, worker=lobby.worker, background=False),
        base_url=lobby.repo.settings.public_origin,
    ) as c:

        def post(path, token, request):
            return c.post(
                "/api/online/v2" + path,
                json=request,
                headers={"Authorization": "Bearer " + token},
            )

        users = [
            c.post("/api/online/v2/sessions", json={"protocolVersion": "2.0"}).json()
            for _ in range(3)
        ]
        tokens = [u["accessToken"] for u in users]
        decks = [
            post("/decks", t, body(ruleReleaseId=RELEASE, entries=ENTRIES)).json()[
                "deckRevision"
            ]
            for t in tokens
        ]
        created = post(
            "/rooms", tokens[0], body(ruleReleaseId=RELEASE, deckRevision=decks[0])
        )
        assert created.status_code == 201
        room = created.json()
        joined = post(
            "/rooms/join",
            tokens[1],
            body(inviteCode=room["inviteCode"], deckRevision=decks[1]),
        )
        assert joined.status_code == 200 and joined.json()["viewerSeat"] == "PLAYER2"
        path = "/rooms/" + room["roomId"]
        assert (
            c.get(
                "/api/online/v2" + path,
                headers={"Authorization": "Bearer " + tokens[2]},
            ).status_code
            == 403
        )
        for extra in ({"seat": "PLAYER1"}, {"seed": 1}, {"userId": users[0]["userId"]}):
            assert (
                post(
                    path + "/ready",
                    tokens[1],
                    {**body(expectedRoomVersion=2, ready=True), **extra},
                ).status_code
                == 422
            )
        assert (
            post(
                path + "/ready", tokens[0], body(expectedRoomVersion=2.0, ready=True)
            ).status_code
            == 422
        )
        assert (
            post(
                path + "/ready", tokens[0], body(expectedRoomVersion=2, ready=True)
            ).status_code
            == 200
        )
        ready = body(expectedRoomVersion=3, ready=True)
        result = post(path + "/ready", tokens[1], ready)
        assert result.status_code == 200 and result.json()["status"] == "initializing"
        assert post(path + "/ready", tokens[1], ready).json() == result.json()
        Sessions(lobby.repo).revoke(tokens[1])
        assert post(path + "/ready", tokens[1], ready).status_code == 401


def test_two_joiners_race_and_response_loss_do_not_create_third_member(lobby):
    a, da, _ = player(lobby)
    b, db, _ = player(lobby)
    c, dc, _ = player(lobby)
    room = lobby.create(a, body(ruleReleaseId=RELEASE, deckRevision=da))
    requests = [body(inviteCode=room["inviteCode"], deckRevision=d) for d in (db, dc)]

    def join(args):
        token, request = args
        try:
            return lobby.join(token, request)
        except ServiceError as e:
            return e.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(join, zip((b, c), requests)))
    assert sum(r == "FORBIDDEN" for r in results) == 1
    winner = 0 if isinstance(results[0], dict) else 1
    assert lobby.join((b, c)[winner], requests[winner]) == results[winner]
    with lobby.repo.db() as sql:
        assert (
            sql.execute("SELECT count(*) AS n FROM ptcg_v2.room_members").fetchone()[
                "n"
            ]
            == 2
        )


def test_busy_worker_defers_without_consuming_failure_budget(lobby):
    prepare(lobby)

    class Busy(SyntheticWorker):
        def initialize(self, job):
            raise ServiceError("CAPACITY_EXCEEDED", 503)

    initializer = Initializer(lobby.repo, Busy())
    for _ in range(4):
        assert not initializer.tick()
        with lobby.repo.db() as db:
            row = db.execute(
                "SELECT attempts,status FROM ptcg_v2.initializations"
            ).fetchone()
            assert row == {"attempts": 0, "status": "pending"}
            db.execute(
                "UPDATE ptcg_v2.initializations SET retry_at=now()-interval '1 second'"
            )


def test_failed_ready_transaction_leaves_no_receipt_job_or_changed_room(
    lobby, monkeypatch
):
    a, b, room, _ = pair(lobby)
    room = lobby.change(
        a, room["roomId"], "ready", body(expectedRoomVersion=2, ready=True)
    )
    request = body(expectedRoomVersion=3, ready=True)

    def fail(*args):
        raise RuntimeError("simulate failure after job insert")

    with monkeypatch.context() as m:
        m.setattr(lobby, "_view", fail)
        with pytest.raises(RuntimeError):
            lobby.change(b, room["roomId"], "ready", request)
    with lobby.repo.db() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.initializations").fetchone()[
                "n"
            ]
            == 0
        )
        assert not db.execute(
            "SELECT 1 FROM ptcg_v2.lobby_receipts WHERE request_id=%s",
            (request["requestId"],),
        ).fetchone()
    assert lobby.get(b, room["roomId"])["roomVersion"] == 3
    assert lobby.change(b, room["roomId"], "ready", request)["status"] == "initializing"


def test_two_jobs_claimed_by_different_initializers_do_not_mix_records(lobby):
    a, _, room1, _ = prepare(lobby)
    c, _, room2, _ = prepare(lobby)
    one = Initializer(lobby.repo, lobby.worker)
    two = Initializer(lobby.repo, SyntheticWorker())
    with ThreadPoolExecutor(2) as pool:
        jobs = [f.result() for f in [pool.submit(one.claim), pool.submit(two.claim)]]
    assert jobs[0]["match_id"] != jobs[1]["match_id"]
    for job in reversed(jobs):
        assert one.finish(job, lobby.worker.initialize(job))
    with lobby.repo.db() as db:
        rows = db.execute(
            "SELECT seed,recovery FROM ptcg_v2.initializations"
        ).fetchall()
        assert len(rows) == 2 and all(r["seed"] == r["recovery"]["seed"] for r in rows)
    assert (
        lobby.get(a, room1["roomId"])["matchId"]
        != lobby.get(c, room2["roomId"])["matchId"]
    )


def test_invite_guess_budget_is_durable_and_forwarded_ip_cannot_reset(lobby):
    limited = replace(lobby.repo.settings, join_per_minute=1)
    for _ in range(2):
        with TestClient(
            create_app(limited, background=False), base_url=limited.public_origin
        ) as c:
            response = c.post(
                "/api/online/v2/rooms/join",
                json=body(inviteCode="AAAAAAAAAA", deckRevision="0" * 32),
                headers={"X-Forwarded-For": "203.0.113.2"},
            )
        assert response.status_code == (401 if _ == 0 else 429)

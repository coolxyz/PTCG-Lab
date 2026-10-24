"""Transactional match acceptance with original synthetic views and real SQL."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
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
from ptcg_online_server.matches import Matches
from ptcg_online_server.repository import Repository
from ptcg_protocol.validation import validate
from tests.protocol.test_projection import original
from tests.online_v2.test_lobby import SyntheticWorker, RELEASE, prepare, player, body


class GameWorker(SyntheticWorker):
    def initialize(self, job):
        return {
            "schema": "core-recovery-v2",
            "implementation": RELEASE,
            "version": 0,
            "seed": job["seed"],
            "commands": [],
        }

    def project(self, record):
        views = {}
        n = record["version"]
        for seat in ("PLAYER1", "PLAYER2"):
            view = original()
            view["observation"]["viewer"] = seat.lower()
            view["observation"]["self"]["id"] = seat.lower()
            view["observation"]["opponent"]["id"] = (
                "player2" if seat == "PLAYER1" else "player1"
            )
            view["observation"]["self"]["hand"] = [
                {"name": "private-" + seat, "kind": "card"}
            ]
            view["observation"]["self"]["hand_count"] = 1
            view["stateVersion"] = n
            view["done"] = n >= 4
            view["winner"] = "PLAYER1" if n >= 4 else None
            view["decision"] = None
            if n < 4 and seat == ("PLAYER1" if n % 2 == 0 else "PLAYER2"):
                view["decision"] = {
                    "id": f"d{n}",
                    "actor": seat,
                    "kind": "options",
                    "options": [{"id": f"d{n}:o0", "actionType": "PassTurn"}],
                }
            views[seat] = view
        return {
            "recovery": deepcopy(record),
            "views": views,
            "effects": {s: [] for s in views},
            "results": {s: None for s in views},
        }

    def command(self, record, seat, command):
        value = deepcopy(record)
        value["version"] += 1
        value["commands"].append({"seat": seat, "command": command})
        return self.project(value)


@pytest.fixture
def game(repo):
    repo = Repository(replace(repo.settings, approved_rule_release_id=RELEASE))
    worker = GameWorker()
    lobby = Lobby(repo, worker)
    a, b, room, _ = prepare(lobby)
    assert Initializer(repo, worker).tick()
    service = Matches(repo, worker)
    assert service.promote_one()
    controllers = [uuid.uuid4().hex for _ in range(2)]
    for token, controller in zip((a, b), controllers):
        service.control(
            token,
            room["matchId"],
            body(controllerId=controller, expectedControllerEpoch=0),
        )
    return service, lobby, (a, b), room["matchId"], controllers


def command(view, id=None):
    return {
        "protocolVersion": "2.0",
        "commandId": id or uuid.uuid4().hex,
        "expectedMatchSeq": view["matchSeq"],
        "expectedStateVersion": view["stateVersion"],
        "controllerEpoch": view["controllerEpoch"],
        "decisionId": view["decision"]["id"],
        "choice": {"optionId": view["decision"]["options"][0]["id"]},
    }


def test_command_receipt_retry_conflict_and_private_replay(game):
    service, lobby, tokens, mid, controllers = game
    first = command(service.get(tokens[0], mid))
    receipt = service.command(tokens[0], mid, first, controllers[0])
    assert (
        receipt
        == service.command(tokens[0], mid, first, controllers[0])
        == service.receipt(tokens[0], mid, first["commandId"])
    )
    with pytest.raises(ServiceError, match="IDEMPOTENCY_CONFLICT"):
        service.command(
            tokens[0], mid, {**first, "choice": {"optionId": "changed"}}, controllers[0]
        )
    for n in range(1, 4):
        i = n % 2
        service.command(
            tokens[i], mid, command(service.get(tokens[i], mid)), controllers[i]
        )
    own = service.get(tokens[0], mid)
    assert own["matchSeq"] == 4 and own["done"] and own["winner"] == "PLAYER1"
    page = service.frames(tokens[0], mid)
    assert len(page["frames"]) == 3 and page["nextCursor"]
    tail = service.frames(tokens[0], mid, page["nextCursor"])
    assert len(tail["frames"]) == 2 and tail["nextCursor"] is None
    validate("ReplayPage", page)
    assert all("recovery" not in frame for frame in page["frames"])
    wire = json.dumps(page)
    assert all(
        word not in wire
        for word in ("decision", "seed", "stateDigest", "private-PLAYER2")
    )
    with pytest.raises(ServiceError, match="RESYNC_REQUIRED"):
        service.frames(tokens[1], mid, page["nextCursor"])
    c, _, _ = player(lobby)
    for run in (
        lambda: service.get(c, mid),
        lambda: service.frames(c, mid),
        lambda: service.receipt(c, mid, first["commandId"]),
    ):
        with pytest.raises(ServiceError, match="FORBIDDEN"):
            run()
    # Terminal membership releases lobby admission without erasing replay access.
    with service.repo.db() as db:
        assert (
            db.execute(
                "SELECT status FROM ptcg_v2.rooms WHERE match_id=%s", (mid,)
            ).fetchone()["status"]
            == "closed"
        )


def test_control_cursor_is_independent_and_old_controller_cannot_commit(game):
    service, _, tokens, mid, controllers = game
    before = service.get(tokens[0], mid)
    next_controller = uuid.uuid4().hex
    takeover = body(controllerId=next_controller, expectedControllerEpoch=1)
    after = service.control(tokens[0], mid, takeover)
    assert after["matchSeq"] == before["matchSeq"] and after["controllerEpoch"] == 2
    assert after["streamCursor"] != before["streamCursor"]
    assert service.control(tokens[0], mid, takeover) == after
    updates = service.stream(tokens[0], mid, before["streamCursor"])
    assert len(updates) == 1 and updates[0]["kind"] == "control"
    with pytest.raises(ServiceError, match="CONTROL_LOST"):
        service.command(tokens[0], mid, command(before), controllers[0])
    service.command(tokens[0], mid, command(after), next_controller)


def test_atomic_rollback_after_first_frame_and_retry(game, monkeypatch):
    service, _, tokens, mid, controllers = game
    request = command(service.get(tokens[0], mid))
    original_publish = service._publish

    def fail(db, member, *args, **kwargs):
        result = original_publish(db, member, *args, **kwargs)
        if member["seat"] == "PLAYER2":
            raise RuntimeError("simulated commit failure")
        return result

    with monkeypatch.context() as m:
        m.setattr(service, "_publish", fail)
        with pytest.raises(RuntimeError):
            service.command(tokens[0], mid, request, controllers[0])
    with service.repo.db() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.match_frames").fetchone()["n"]
            == 2
        )
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.match_receipts").fetchone()[
                "n"
            ]
            == 0
        )
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.match_outbox").fetchone()["n"]
            == 4
        )
        assert (
            db.execute("SELECT recovery FROM ptcg_v2.matches").fetchone()["recovery"][
                "version"
            ]
            == 0
        )
    assert service.command(tokens[0], mid, request, controllers[0])["matchSeq"] == 1


@pytest.mark.parametrize("same", [True, False])
def test_concurrent_candidates_commit_once(game, same):
    service, _, tokens, mid, controllers = game
    original_call = service.worker.command
    barrier = threading.Barrier(2)

    def compute(*args):
        barrier.wait(timeout=5)
        return original_call(*args)

    service.worker.command = compute
    one = command(service.get(tokens[0], mid))
    two = one if same else {**one, "commandId": uuid.uuid4().hex}

    def submit(request):
        try:
            return service.command(tokens[0], mid, request, controllers[0])
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, [one, two]))
    if same:
        assert results[0] == results[1]
    else:
        assert results.count("MATCH_VERSION_CONFLICT") == 1
    assert service.get(tokens[0], mid)["matchSeq"] == 1


@pytest.mark.parametrize("during", ["takeover", "revoke", "abort"])
def test_candidate_fenced_by_change_during_rule_computation(game, during):
    service, _, tokens, mid, controllers = game
    request = command(service.get(tokens[0], mid))
    original_call = service.worker.command

    def compute(*args):
        candidate = original_call(*args)
        if during == "takeover":
            service.control(
                tokens[0],
                mid,
                body(controllerId=uuid.uuid4().hex, expectedControllerEpoch=1),
            )
        elif during == "revoke":
            Sessions(service.repo).revoke(tokens[0])
        else:
            assert service.technical_abort(mid, 0)
        return candidate

    service.worker.command = compute
    with pytest.raises(
        ServiceError,
        match={
            "takeover": "CONTROL_LOST",
            "revoke": "UNAUTHORIZED",
            "abort": "MATCH_VERSION_CONFLICT",
        }[during],
    ):
        service.command(tokens[0], mid, request, controllers[0])
    with service.repo.db() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.match_receipts").fetchone()[
                "n"
            ]
            == 0
        )
        row = db.execute("SELECT recovery,status FROM ptcg_v2.matches").fetchone()
        assert row["recovery"]["version"] == 0
        assert row["status"] == ("aborted" if during == "abort" else "playing")


def test_invalid_action_wrong_seat_and_resignation_preserve_recovery(game):
    service, _, tokens, mid, controllers = game
    request = command(service.get(tokens[0], mid))
    with pytest.raises(ServiceError, match="NOT_ACTOR"):
        service.command(tokens[1], mid, request, controllers[1])
    with pytest.raises(ServiceError, match="INVALID_OPTION"):
        service.command(
            tokens[0],
            mid,
            {**request, "choice": {"optionId": "forged"}},
            controllers[0],
        )
    view = service.get(tokens[1], mid)
    resign = {
        "protocolVersion": "2.0",
        "commandId": uuid.uuid4().hex,
        "expectedMatchSeq": 0,
        "expectedStateVersion": 0,
        "controllerEpoch": view["controllerEpoch"],
    }
    accepted = service.resign(tokens[1], mid, resign, controllers[1])
    assert service.resign(tokens[1], mid, resign, controllers[1]) == accepted
    result = service.get(tokens[1], mid)
    assert (
        result["done"]
        and result["winner"] == "PLAYER1"
        and result["result"]["reason"] == "resigned"
    )
    assert result["stateVersion"] == 0 and result["matchSeq"] == 1


def test_outbox_retention_and_cross_identity_cursor_fail_closed(game):
    service, _, tokens, mid, controllers = game
    before = service.get(tokens[0], mid)
    limited = Matches(
        Repository(replace(service.repo.settings, outbox_retention=2)), service.worker
    )
    for epoch in range(1, 4):
        limited.control(
            tokens[0],
            mid,
            body(controllerId=uuid.uuid4().hex, expectedControllerEpoch=epoch),
        )
    with pytest.raises(ServiceError, match="RESYNC_REQUIRED"):
        limited.stream(tokens[0], mid, before["streamCursor"])
    with pytest.raises(ServiceError, match="RESYNC_REQUIRED"):
        limited.stream(tokens[1], mid, limited.get(tokens[0], mid)["streamCursor"])
    latest = limited.stream(tokens[0], mid, None)[0]["snapshot"]
    assert limited.stream(tokens[0], mid, latest["streamCursor"]) == []


def test_match_http_and_websocket_auth_snapshot_resume_and_revoke(game):
    service, _, tokens, mid, controllers = game
    settings = service.repo.settings
    with TestClient(
        create_app(settings, worker=service.worker, background=False),
        base_url=settings.public_origin,
    ) as client:
        headers = {"Authorization": "Bearer " + tokens[0], "Host": settings.host}
        with client.websocket_connect("/api/online/v2/stream", headers=headers) as ws:
            ws.send_json(
                {"protocolVersion": "2.0", "matchId": mid, "streamCursor": None}
            )
            initial = ws.receive_json()
            assert (
                initial["type"] == "snapshot"
                and initial["snapshot"]["viewerSeat"] == "PLAYER1"
            )
            request = command(initial["snapshot"])
            response = client.post(
                "/api/online/v2/matches/" + mid + "/commands",
                headers={**headers, "X-PTCG-Controller": controllers[0]},
                json=request,
            )
            assert response.status_code == 200
            pushed = ws.receive_json()
            assert pushed["type"] == "update" and pushed["snapshot"]["matchSeq"] == 1
            assert "private-PLAYER2" not in json.dumps(pushed)
        with client.websocket_connect(
            "/api/online/v2/stream", headers=headers
        ) as resumed:
            resumed.send_json(
                {
                    "protocolVersion": "2.0",
                    "matchId": mid,
                    "streamCursor": initial["snapshot"]["streamCursor"],
                }
            )
            assert resumed.receive_json() == pushed
            Sessions(service.repo).revoke(tokens[0])
            assert resumed.receive_json()["code"] == "UNAUTHORIZED"
        for extra in ({"Origin": "https://evil.test"}, {"Cookie": "legacy=secret"}):
            with pytest.raises(Exception):
                with client.websocket_connect(
                    "/api/online/v2/stream", headers={**headers, **extra}
                ):
                    pass


def test_command_capacity_does_not_prevent_resignation(game):
    service, _, tokens, mid, controllers = game
    service = Matches(
        Repository(replace(service.repo.settings, max_match_commands=1)), service.worker
    )
    service.command(
        tokens[0], mid, command(service.get(tokens[0], mid)), controllers[0]
    )
    v = service.get(tokens[1], mid)
    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
        service.command(tokens[1], mid, command(v), controllers[1])
    request = {
        "protocolVersion": "2.0",
        "commandId": "stop",
        "expectedMatchSeq": 1,
        "expectedStateVersion": 1,
        "controllerEpoch": 1,
    }
    assert service.resign(tokens[1], mid, request, controllers[1])["matchSeq"] == 2


def test_two_matches_same_external_command_id_stay_isolated(game):
    service, lobby, tokens, mid, controllers = game
    a, b, room, _ = prepare(lobby)
    assert Initializer(service.repo, service.worker).tick()
    assert service.promote_one()
    other = room["matchId"]
    controller = uuid.uuid4().hex
    service.control(a, other, body(controllerId=controller, expectedControllerEpoch=0))
    first = command(service.get(tokens[0], mid), "same-id")
    second = command(service.get(a, other), "same-id")
    with ThreadPoolExecutor(2) as pool:
        pending = [
            pool.submit(service.command, tokens[0], mid, first, controllers[0]),
            pool.submit(service.command, a, other, second, controller),
        ]
        result = [f.result() for f in pending]
    assert {r["matchId"] for r in result} == {mid, other}
    assert (
        service.get(tokens[0], mid)["matchSeq"]
        == service.get(a, other)["matchSeq"]
        == 1
    )
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        service.get(tokens[0], other)


def test_stream_cap_and_malformed_subscription_are_bounded(game):
    service, _, tokens, mid, _ = game
    settings = replace(service.repo.settings, max_streams=1)
    with TestClient(
        create_app(settings, worker=service.worker, background=False),
        base_url=settings.public_origin,
    ) as client:
        headers = {"Authorization": "Bearer " + tokens[0], "Host": settings.host}
        with client.websocket_connect("/api/online/v2/stream", headers=headers) as ws:
            ws.send_json(
                {"protocolVersion": "2.0", "matchId": mid, "streamCursor": None}
            )
            assert ws.receive_json()["type"] == "snapshot"
            with pytest.raises(Exception):
                with client.websocket_connect("/api/online/v2/stream", headers=headers):
                    pass
            ws.send_json(
                {
                    "protocolVersion": "2.0",
                    "type": "ping",
                    "accessToken": "secret-sentinel",
                }
            )
            error = ws.receive_json()
            assert error[
                "code"
            ] == "INVALID_PAYLOAD" and "secret-sentinel" not in json.dumps(error)

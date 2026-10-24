# ruff: noqa: F811
"""Match authorization must be re-evaluated across credential lifecycle changes."""

from dataclasses import replace
import uuid
import pytest
from fastapi.testclient import TestClient
from ptcg_online_server.api import create_app
from ptcg_online_server.auth import Sessions, token_digest
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.initialization import Initializer
from ptcg_online_server.lobby import Lobby
from ptcg_online_server.matches import Matches
from ptcg_online_server.repository import Repository
from tests.online_v2.test_lobby import RELEASE, body, player
from tests.online_v2.test_matches import GameWorker, command, game  # noqa: F401


@pytest.mark.parametrize("expiry", ["access", "session"])
def test_expiry_during_rule_computation_cannot_commit(game, monkeypatch, expiry):
    service, _, tokens, mid, controllers = game
    original = service.worker.command
    request = command(service.get(tokens[0], mid))
    session = Sessions(service.repo).authenticate(tokens[0])["sessionId"]

    def compute(*args):
        result = original(*args)
        with service.repo.db() as db:
            if expiry == "access":
                db.execute(
                    "UPDATE ptcg_v2.access_tokens SET expires_at=now()-interval '1 second' WHERE digest=%s",
                    (token_digest(tokens[0], "atk"),),
                )
            else:
                db.execute(
                    "UPDATE ptcg_v2.sessions SET expires_at=now()-interval '1 second' WHERE id=%s",
                    (session,),
                )
        return result

    monkeypatch.setattr(service.worker, "command", compute)
    with pytest.raises(ServiceError, match="UNAUTHORIZED"):
        service.command(tokens[0], mid, request, controllers[0])
    assert service.get(tokens[1], mid)["matchSeq"] == 0
    with service.repo.db() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.match_receipts").fetchone()[
                "n"
            ]
            == 0
        )
        assert (
            db.execute("SELECT recovery FROM ptcg_v2.matches").fetchone()["recovery"][
                "version"
            ]
            == 0
        )


def test_refresh_closes_old_stream_preserves_control_and_reuse_revokes_new_token(repo):
    repo = Repository(replace(repo.settings, approved_rule_release_id=RELEASE))
    worker = GameWorker()
    lobby = Lobby(repo, worker)
    a, da, credentials = player(lobby)
    b, db, _ = player(lobby)
    room = lobby.create(a, body(ruleReleaseId=RELEASE, deckRevision=da))
    room = lobby.join(b, body(inviteCode=room["inviteCode"], deckRevision=db))
    for token in (a, b):
        room = lobby.change(
            token,
            room["roomId"],
            "ready",
            body(expectedRoomVersion=room["roomVersion"], ready=True),
        )
    assert Initializer(repo, worker).tick()
    matches = Matches(repo, worker)
    assert matches.promote_one()
    mid, controller = room["matchId"], uuid.uuid4().hex
    matches.control(a, mid, body(controllerId=controller, expectedControllerEpoch=0))
    settings = repo.settings
    with TestClient(
        create_app(settings, worker=worker, background=False),
        base_url=settings.public_origin,
    ) as client:
        headers = {"Authorization": "Bearer " + a, "Host": settings.host}
        with client.websocket_connect(
            "/api/online/v2/stream", headers=headers
        ) as stream:
            stream.send_json(
                {"protocolVersion": "2.0", "matchId": mid, "streamCursor": None}
            )
            view = stream.receive_json()["snapshot"]
            rotated = client.post(
                "/api/online/v2/sessions/refresh",
                json={
                    "protocolVersion": "2.0",
                    "refreshToken": credentials["refreshToken"],
                },
            )
            assert rotated.status_code == 200
            new = rotated.json()
            assert new["sessionId"] == credentials["sessionId"]
            assert stream.receive_json()["code"] == "UNAUTHORIZED"
        request = command(view)
        route = "/api/online/v2/matches/" + mid
        assert (
            client.post(
                route + "/commands",
                headers={**headers, "X-PTCG-Controller": controller},
                json=request,
            ).status_code
            == 401
        )
        headers["Authorization"] = "Bearer " + new["accessToken"]
        with client.websocket_connect(
            "/api/online/v2/stream", headers=headers
        ) as stream:
            stream.send_json(
                {
                    "protocolVersion": "2.0",
                    "matchId": mid,
                    "streamCursor": view["streamCursor"],
                }
            )
            accepted = client.post(
                route + "/commands",
                headers={**headers, "X-PTCG-Controller": controller},
                json=request,
            )
            assert accepted.status_code == 200
            assert stream.receive_json()["snapshot"]["matchSeq"] == 1
            reused = client.post(
                "/api/online/v2/sessions/refresh",
                json={
                    "protocolVersion": "2.0",
                    "refreshToken": credentials["refreshToken"],
                },
            )
            assert reused.status_code == 401
            assert stream.receive_json()["code"] == "UNAUTHORIZED"
        # Even an already successful command receipt needs current authorization.
        for path in (
            route,
            route + "/frames",
            route + "/receipts/" + request["commandId"],
        ):
            assert client.get(path, headers=headers).status_code == 401
        assert matches.get(b, mid)["matchSeq"] == 1

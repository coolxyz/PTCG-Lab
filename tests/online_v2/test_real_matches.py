# ruff: noqa: F811
"""Real pinned core, HTTPS/WSS, two identities and process restart acceptance."""

import json
import ssl
import subprocess
import time
from urllib.error import HTTPError
import uuid
import pytest
from websockets.sync.client import connect
from ptcg_online_server.repository import Repository
from ptcg_protocol.validation import validate
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_process import server, call
from tests.online_v2.test_lobby import body


def certificate(tmp_path):
    cert, key = tmp_path / "test-cert.pem", tmp_path / "test-key.pem"
    config = tmp_path / "tls.cnf"
    config.write_text(
        "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n"
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-config",
            str(config),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    key.chmod(0o600)
    return cert, key


def choose(view, ident):
    decision = view["decision"]
    choice = (
        {"selectedRefs": [c["ref"] for c in decision["candidates"][: decision["min"]]]}
        if decision["kind"] == "selection"
        else {"optionId": decision["options"][0]["id"]}
    )
    return {
        "protocolVersion": "2.0",
        "commandId": ident,
        "expectedMatchSeq": view["matchSeq"],
        "expectedStateVersion": view["stateVersion"],
        "controllerEpoch": view["controllerEpoch"],
        "decisionId": decision["id"],
        "choice": choice,
    }


def test_real_https_wss_commands_lost_response_restart_and_private_replay(
    real, tmp_path
):
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    tls = certificate(tmp_path)
    context = ssl.create_default_context(cafile=str(tls[0]))
    controllers = [uuid.uuid4().hex for _ in range(2)]
    with server(settings, tmp_path, tls=tls) as origin:

        def req(path, **kw):
            return call(origin, path, context=context, **kw)

        tokens = [
            req("/sessions", body={"protocolVersion": "2.0"})["accessToken"]
            for _ in range(3)
        ]
        decks = [
            req(
                "/decks",
                access=tokens[i],
                body=body(
                    ruleReleaseId=inputs["release"], entries=inputs["deck" + str(i + 1)]
                ),
            )["deckRevision"]
            for i in range(2)
        ]
        room = req(
            "/rooms",
            access=tokens[0],
            body=body(ruleReleaseId=inputs["release"], deckRevision=decks[0]),
        )
        room = req(
            "/rooms/join",
            access=tokens[1],
            body=body(inviteCode=room["inviteCode"], deckRevision=decks[1]),
        )
        roompath = "/rooms/" + room["roomId"]
        req(
            roompath + "/ready",
            access=tokens[0],
            body=body(expectedRoomVersion=2, ready=True),
        )
        room = req(
            roompath + "/ready",
            access=tokens[1],
            body=body(expectedRoomVersion=3, ready=True),
        )
        mid = room["matchId"]
        path = "/matches/" + mid
        for _ in range(80):
            try:
                req(path, access=tokens[0])
                break
            except HTTPError as error:
                assert error.code == 403
                time.sleep(0.25)
        for i in range(2):
            req(
                path + "/control",
                access=tokens[i],
                body=body(controllerId=controllers[i], expectedControllerEpoch=0),
            )
        uri = origin.replace("https:", "wss:") + "/api/online/v2/stream"
        with connect(
            uri,
            ssl=context,
            proxy=None,
            compression=None,
            additional_headers={"Authorization": "Bearer " + tokens[0]},
            open_timeout=5,
            close_timeout=2,
        ) as ws:
            ws.send(
                json.dumps(
                    {"protocolVersion": "2.0", "matchId": mid, "streamCursor": None}
                )
            )
            initial = json.loads(ws.recv(timeout=5))
            assert initial["snapshot"]["viewerSeat"] == "PLAYER1"
            actor_counts = [0, 0]
            for step in range(8):
                views = [req(path, access=tokens[i]) for i in range(2)]
                for v in views:
                    validate("MatchSnapshot", v)
                i = next(i for i, v in enumerate(views) if v["decision"] is not None)
                # Same external command ID in distinct players' namespaces is valid.
                request = choose(views[i], f"seat-action-{actor_counts[i]}")
                actor_counts[i] += 1
                accepted = req(
                    path + "/commands",
                    access=tokens[i],
                    controller=controllers[i],
                    body=request,
                )
                assert accepted["matchSeq"] == step + 1
                assert (
                    req(
                        path + "/commands",
                        access=tokens[i],
                        controller=controllers[i],
                        body=request,
                    )
                    == accepted
                )
                while True:
                    pushed = json.loads(ws.recv(timeout=5))
                    if pushed.get("type") != "heartbeat":
                        break
                assert (
                    pushed["type"] == "update"
                    and pushed["snapshot"]["matchSeq"] == step + 1
                )
            saved = (i, request, accepted)
            cursor = initial["snapshot"]["streamCursor"]
        with pytest.raises(HTTPError) as outsider:
            req(path, access=tokens[2])
        assert outsider.value.code == 403
    # Abrupt API exit occurred after acceptance; all game data is in the new DB.
    with server(settings, tmp_path, tls=tls) as origin:

        def req(path, **kw):
            return call(origin, path, context=context, **kw)

        i, request, accepted = saved
        assert (
            req(path + "/receipts/" + request["commandId"], access=tokens[i])
            == accepted
        )
        assert (
            req(
                path + "/commands",
                access=tokens[i],
                controller=controllers[i],
                body=request,
            )
            == accepted
        )
        snapshot = req(path, access=tokens[0])
        assert snapshot["matchSeq"] == 8
        uri = origin.replace("https:", "wss:") + "/api/online/v2/stream"
        with connect(
            uri,
            ssl=context,
            proxy=None,
            additional_headers={"Authorization": "Bearer " + tokens[0]},
            close_timeout=2,
        ) as ws:
            ws.send(
                json.dumps(
                    {"protocolVersion": "2.0", "matchId": mid, "streamCursor": cursor}
                )
            )
            replayed = [json.loads(ws.recv(timeout=5)) for _ in range(8)]
            assert [e["snapshot"]["matchSeq"] for e in replayed] == list(range(1, 9))
        resign = {
            "protocolVersion": "2.0",
            "commandId": "resign-after-restart",
            "expectedMatchSeq": 8,
            "expectedStateVersion": 8,
            "controllerEpoch": 1,
        }
        req(path + "/resign", access=tokens[1], controller=controllers[1], body=resign)
        end = req(path, access=tokens[0])
        assert end["done"] and end["winner"] == "PLAYER1"
        count = 0
        route = path + "/frames"
        while True:
            page = req(route, access=tokens[0])
            validate("ReplayPage", page)
            count += len(page["frames"])
            assert all(
                "decision" not in f and "recovery" not in f for f in page["frames"]
            )
            if not page["nextCursor"]:
                break
            route = path + "/frames/" + page["nextCursor"]
        assert count == 10
        timeline = req(path + "/timeline", access=tokens[0])
        assert len(timeline["events"]) == 3

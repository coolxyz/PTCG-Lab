# ruff: noqa: F811
"""Two actual loopback HTTP bridges to an isolated real TLS API and rule worker."""

from contextlib import contextmanager, ExitStack
import json
import socket
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import uvicorn
from ptcg_desktop_api.api import create_app, PREFIX
from ptcg_desktop_api.bridge import Bridge
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server
from ptcg_online_server.repository import Repository
from tests.online_v2.conftest import settings  # noqa: F401
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_process import server
from tests.online_v2.test_real_matches import certificate, choose


@contextmanager
def local_bridge(profile, storage):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    owner = Bridge({"lan": profile}, storage)
    token = owner.issue_window()
    runner = uvicorn.Server(
        uvicorn.Config(
            create_app(owner, origin=origin),
            access_log=False,
            log_level="critical",
            proxy_headers=False,
            lifespan="on",
        )
    )
    thread = Thread(target=runner.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))

    def call(action, body=None, *, capability=token):
        request = Request(
            origin + PREFIX + "/profiles/lan/" + action,
            data=json.dumps(body or {}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": origin,
                "X-PTCG-Bridge": capability,
            },
        )
        try:
            with opener.open(request, timeout=20) as result:
                return result.status, json.loads(result.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        for _ in range(100):
            if runner.started:
                break
            assert thread.is_alive()
            time.sleep(0.02)
        assert runner.started
        yield owner, call
    finally:
        runner.should_exit = True
        thread.join(timeout=25)
        sock.close()
        assert not thread.is_alive(), "Isolated bridge failed to stop"


def test_two_http_bridges_invite_duel_private_stream_recovery_and_fencing(
    real, tmp_path
):
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    tls = certificate(tmp_path)
    with server(settings, tmp_path, tls=tls) as origin, ExitStack() as stack:
        profile = Server(origin, ca_file=str(tls[0]))
        devices = [
            stack.enter_context(local_bridge(profile, tmp_path / f"device-{i}"))
            for i in range(2)
        ]

        def ok(i, action, body=None, **kw):
            status, payload = devices[i][1](action, body, **kw)
            assert status == 200, payload
            text = json.dumps(payload)
            assert "accessToken" not in text and "refreshToken" not in text
            return payload["result"]

        identities = [ok(i, "guest") for i in range(2)]
        assert identities[0]["userId"] != identities[1]["userId"]
        revisions = [
            ok(
                i,
                "deck",
                {
                    "entries": inputs[f"deck{i+1}"],
                    "ruleReleaseId": inputs["release"],
                    "requestId": f"deck-{i}",
                },
            )["deckRevision"]
            for i in range(2)
        ]
        room = ok(
            0,
            "create-room",
            {
                "deckRevision": revisions[0],
                "ruleReleaseId": inputs["release"],
                "requestId": "invite",
            },
        )
        room = ok(
            1,
            "join-room",
            {
                "deckRevision": revisions[1],
                "inviteCode": room["inviteCode"],
                "requestId": "join",
            },
        )
        for i in range(2):
            room = ok(
                i,
                "ready",
                {
                    "roomId": room["roomId"],
                    "expectedRoomVersion": room["roomVersion"],
                    "ready": True,
                    "requestId": f"ready-{i}",
                },
            )
        mid = room["matchId"]
        for _ in range(100):
            code, _ = devices[0][1]("snapshot", {"matchId": mid})
            if code == 200:
                break
            assert code == 403
            time.sleep(0.1)
        leases = [
            ok(i, "control", {"matchId": mid, "expectedControllerEpoch": 0})["lease"]
            for i in range(2)
        ]
        for i in range(2):
            ok(i, "watch", {"matchId": mid})
        views = [ok(i, "snapshot", {"matchId": mid}) for i in range(2)]
        assert views[1]["viewerSeat"] == "PLAYER2"
        actor = next(i for i, v in enumerate(views) if v["decision"])
        # Another local window takes control; the old window cannot send a command.
        new_window = devices[actor][0].issue_window()
        control = ok(
            actor,
            "control",
            {
                "matchId": mid,
                "expectedControllerEpoch": views[actor]["controllerEpoch"],
            },
            capability=new_window,
        )
        payload = choose(control["snapshot"], "bridge-first")
        status, result = devices[actor][1](
            "command", {"matchId": mid, "lease": leases[actor], "command": payload}
        )
        assert status == 409 and result["error"]["code"] == "LOCAL_CONTROL_REQUIRED"
        receipt = ok(
            actor,
            "command",
            {"matchId": mid, "lease": control["lease"], "command": payload},
            capability=new_window,
        )
        assert receipt["matchSeq"] == 1
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            feed_state = ok(1, "events", {"matchId": mid})
            assert not feed_state["done"], {
                "error": feed_state["error"],
                "revision": feed_state["revision"],
            }
            event = feed_state["event"]
            if event and event["snapshot"]["matchSeq"] == 1:
                break
            time.sleep(0.05)
        assert (
            event["snapshot"]["matchSeq"] == 1
            and event["snapshot"]["viewerSeat"] == "PLAYER2"
        )
        # Simulate remote reply loss behind the bridge, preserving the server commit.
        views = [ok(i, "snapshot", {"matchId": mid}) for i in range(2)]
        actor = next(i for i, v in enumerate(views) if v["decision"])
        control = ok(
            actor,
            "control",
            {
                "matchId": mid,
                "expectedControllerEpoch": views[actor]["controllerEpoch"],
            },
        )
        client = devices[actor][0].profiles["lan"].client
        original = client.transport

        class Drop:
            def stream(self, *args):
                return original.stream(*args)

            def request(self, method, path, *args):
                result = original.request(method, path, *args)
                if path.endswith("/commands"):
                    raise ClientError("NETWORK_UNAVAILABLE", retryable=True)
                return result

        client.transport = Drop()
        status, result = devices[actor][1](
            "command",
            {
                "matchId": mid,
                "lease": control["lease"],
                "command": choose(control["snapshot"], "bridge-lost"),
            },
        )
        assert status == 409 and result["error"]["code"] == "NETWORK_UNAVAILABLE"
        assert ok(actor, "status")["pendingMatches"] == [mid]
        assert ok(actor, "recover", {"matchId": mid})["receipt"]["matchSeq"] == 2
        with repo.db() as db:
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM ptcg_v2.match_receipts"
                ).fetchone()["n"]
                == 2
            )
        client.transport = original
        view = ok(actor, "snapshot", {"matchId": mid})
        ok(
            actor,
            "resign",
            {
                "matchId": mid,
                "lease": control["lease"],
                "command": {
                    "protocolVersion": "2.0",
                    "commandId": "bridge-resign",
                    "expectedMatchSeq": 2,
                    "expectedStateVersion": 2,
                    "controllerEpoch": view["controllerEpoch"],
                },
            },
        )
        for i in range(2):
            page = ok(i, "replay", {"matchId": mid})
            assert page["viewerSeat"] == f"PLAYER{i+1}"
            tail = ok(i, "replay", {"matchId": mid, "cursor": page["nextCursor"]})
            assert tail["frames"][0]["result"]["reason"] == "resigned"
            ok(i, "unwatch", {"matchId": mid})

# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import json
import time
import pytest
from ptcg_online_client.client import Client
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server
from ptcg_online_server.repository import Repository
from tests.online_v2.conftest import settings  # noqa: F401
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_process import server
from tests.online_v2.test_real_matches import certificate, choose
from tests.online_client.test_client import expire


def test_native_package_real_tls_duel_stream_refresh_and_restart_recovery(
    real, tmp_path
):
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    tls = certificate(tmp_path)
    with server(settings, tmp_path, tls=tls) as origin:
        profile = Server(origin, ca_file=str(tls[0]))
        clients = [Client(profile, tmp_path / f"device-{i}") for i in range(2)]
        try:
            identities = [c.create_guest() for c in clients]
            assert identities[0]["userId"] != identities[1]["userId"]
            assert not any("Token" in k for identity in identities for k in identity)
            revisions = [
                c.submit_deck(inputs[f"deck{i+1}"], inputs["release"])["deckRevision"]
                for i, c in enumerate(clients)
            ]
            room = clients[0].create_room(revisions[0], inputs["release"])
            room = clients[1].join_room(room["inviteCode"], revisions[1])
            for c in clients:
                room = c.change_room(
                    room["roomId"], "ready", room["roomVersion"], ready=True
                )
            mid = room["matchId"]
            for _ in range(100):
                try:
                    clients[0].snapshot(mid)
                    break
                except ClientError as e:
                    assert e.status == 403
                    time.sleep(0.2)
            for c in clients:
                c.take_control(mid, 0)
            stream = clients[1].events(mid)
            assert next(stream)["snapshot"]["viewerSeat"] == "PLAYER2"
            with ThreadPoolExecutor(max_workers=1) as pool:
                update = pool.submit(next, stream)
                views = [c.snapshot(mid) for c in clients]
                actor = next(i for i, v in enumerate(views) if v["decision"])
                accepted = clients[actor].submit(
                    mid, choose(views[actor], "native-first")
                )
                assert accepted["matchSeq"] == 1
                assert update.result(timeout=15)["snapshot"]["matchSeq"] == 1
            stream.close()
            # Refresh merging uses the actual server's strict one-use refresh policy.
            expire(clients[0])
            with ThreadPoolExecutor(max_workers=6) as pool:
                assert all(
                    v["matchSeq"] == 1
                    for v in pool.map(lambda _: clients[0].snapshot(mid), range(6))
                )
            with repo.db() as db:
                assert (
                    db.execute(
                        "SELECT count(*) AS n FROM ptcg_v2.refresh_tokens"
                    ).fetchone()["n"]
                    == 3
                )
            views = [c.snapshot(mid) for c in clients]
            actor = next(i for i, v in enumerate(views) if v["decision"])
            pending = choose(views[actor], "native-lost-ack")
            transport = clients[actor].transport

            class LoseAck:
                def request(self, method, path, *args):
                    result = transport.request(method, path, *args)
                    if path.endswith("/commands"):
                        raise ClientError("NETWORK_UNAVAILABLE", retryable=True)
                    return result

            clients[actor].transport = LoseAck()
            with pytest.raises(ClientError, match="NETWORK_UNAVAILABLE"):
                clients[actor].submit(mid, pending)
            clients[actor].close()
            clients[actor] = Client(profile, tmp_path / f"device-{actor}")
            assert clients[actor].recover(mid)["matchSeq"] == 2
            view = clients[actor].snapshot(mid)
            assert view["matchSeq"] == 2
            with repo.db() as db:
                assert (
                    db.execute(
                        "SELECT count(*) AS n FROM ptcg_v2.match_receipts"
                    ).fetchone()["n"]
                    == 2
                )
            resigned = {
                "protocolVersion": "2.0",
                "commandId": "native-resign",
                "expectedMatchSeq": 2,
                "expectedStateVersion": 2,
                "controllerEpoch": view["controllerEpoch"],
            }
            clients[actor].submit(mid, resigned, resign=True)
            for i, c in enumerate(clients):
                page = c.replay(mid)
                assert page["viewerSeat"] == f"PLAYER{i+1}" and len(page["frames"]) == 3
                tail = c.replay(mid, page["nextCursor"])
                assert tail["frames"][0]["result"]["reason"] == "resigned"
                assert "accessToken" not in json.dumps(c.status())
        finally:
            for c in clients:
                c.close()

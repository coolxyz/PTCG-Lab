# ruff: noqa: F811
"""P4-D natural termination and precise real-process failure boundaries."""

from concurrent.futures import ThreadPoolExecutor
from http.client import RemoteDisconnected
import json
import os
import signal
import ssl
import time
from urllib.error import HTTPError, URLError
import pytest
from ptcg_online_server.repository import Repository
from ptcg_protocol.projection import replay_page
from ptcg_protocol.validation import validate
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_real_matches import certificate, choose
from tests.online_v2.test_process import server
from tests.online_v2.native_pair import NativePair, pushed, report


def private(view, seat):
    validate("MatchSnapshot", view)
    assert view["viewerSeat"] == seat
    assert view["observation"]["opponent"]["hand"] is None
    assert "recovery" not in view and "stateDigest" not in view and "seed" not in view
    if view["decision"]:
        assert view["decision"]["actor"] == seat


def pass_choice(view, step):
    command = choose(view, f"natural-{step}")
    if view["decision"]["kind"] == "options":
        options = view["decision"]["options"]
        option = next((o for o in options if o["actionType"] == "PassTurn"), options[0])
        command["choice"] = {"optionId": option["id"]}
    return command


def test_two_native_players_reach_natural_end_and_replay_every_visible_frame(
    real, tmp_path
):
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    tls = certificate(tmp_path)
    context = ssl.create_default_context(cafile=str(tls[0]))
    with server(settings, tmp_path, tls=tls) as origin:
        pair = NativePair(origin, context)
        pair.prepare(inputs)
        sockets = [pair.stream(i) for i in range(2)]
        try:
            views = [pushed(ws) for ws in sockets]
            history = [[replay_page([v])["frames"][0]] for v in views]
            actors = [0, 0]
            for step in range(1, 180):
                i = next(i for i, v in enumerate(views) if v["decision"] is not None)
                receipt = pair.submit(i, pass_choice(views[i], step))
                actors[i] += 1
                assert receipt["matchSeq"] == step
                views = [pushed(ws) for ws in sockets]
                for n, v in enumerate(views):
                    private(v, f"PLAYER{n+1}")
                    assert v["matchSeq"] == step
                    if step % 10 == 0 or v["done"]:
                        assert v == pair.view(n)
                    history[n].append(replay_page([v])["frames"][0])
                if step == 20:
                    sockets[1].close()
                    sockets[1] = pair.stream(1, views[1]["streamCursor"])
                if step % 25 == 0:
                    print(f"P4-D natural match: {step} accepted commands", flush=True)
                if all(v["done"] for v in views):
                    break
            else:
                pytest.fail("No natural end within bounded legal pass policy")
            assert all(actors) and views[0]["winner"] == views[1]["winner"]
            assert views[0]["winner"] in ("PLAYER1", "PLAYER2")
            reason = views[0]["result"]["reason"]
            assert reason not in ("unknown", "resigned", "technical_abort", "truncated")
            assert (
                pair.request(pair.roompath, access=pair.access[0])["status"] == "closed"
            )
            for i in range(2):
                collected, route = [], pair.path + "/frames"
                while True:
                    page = pair.request(route, access=pair.access[i])
                    validate("ReplayPage", page)
                    assert page["viewerSeat"] == f"PLAYER{i+1}"
                    collected.extend(page["frames"])
                    if page["nextCursor"] is None:
                        break
                    route = pair.path + "/frames/" + page["nextCursor"]
                # Every historical frame is exactly what that user received live,
                # after removing only actionable/control protocol fields.
                assert collected == history[i]
            report(
                "natural-match",
                {
                    "acceptedCommands": step,
                    "commandsBySeat": actors,
                    "visibleFramesCompared": sum(map(len, history)),
                    "reason": reason,
                    "transport": "HTTPS/WSS",
                    "privateReplays": 2,
                    "natural": True,
                    "maxHandshakeSeconds": round(max(pair.handshake_seconds), 3),
                },
            )
        finally:
            for ws in sockets:
                ws.close()


def wait_file(path):
    until = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < until, "Dedicated fault barrier was not reached"
        time.sleep(0.03)


def initial_record(repo):
    with repo.db() as db:
        return db.execute(
            "SELECT seed,revision1,revision2,recovery FROM ptcg_v2.initializations"
        ).fetchone()


@pytest.mark.parametrize(
    "fault", ["api-before-commit", "rule-before-commit", "api-after-commit"]
)
def test_actual_process_death_and_unknown_response_never_reapply(real, tmp_path, fault):
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    tls = certificate(tmp_path)
    context = ssl.create_default_context(cafile=str(tls[0]))
    children = []
    committed = fault == "api-after-commit"
    with server(
        settings,
        tmp_path,
        tls=tls,
        factory="tests.online_v2.fault_server:create_app",
        extra_env={
            "PTCG_TEST_FAULT_DIRECTORY": str(tmp_path),
            "PTCG_TEST_FAULT_MODE": "response" if committed else "compute",
        },
        on_started=children.append,
    ) as origin:
        pair = NativePair(origin, context)
        pair.prepare(inputs)
        first = initial_record(repo)
        views = [pair.view(i) for i in range(2)]
        actor = next(i for i, v in enumerate(views) if v["decision"])
        request = choose(views[actor], "pending-through-process-loss")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(pair.submit, actor, request)
            wait_file(tmp_path / ("committed" if committed else "worker-pid"))
            if not committed:
                process = json.loads((tmp_path / "worker-pid").read_text())
                assert process["parent"] == children[0].pid
            if fault == "rule-before-commit":
                os.kill(process["pid"], signal.SIGKILL)
                with pytest.raises(HTTPError) as error:
                    pending.result(timeout=5)
                assert error.value.code == 503
                assert json.load(error.value)["code"] == "RULE_RELEASE_UNAVAILABLE"
            else:
                children[0].kill()
                children[0].wait(timeout=5)
                with pytest.raises((RemoteDisconnected, URLError, ConnectionError)):
                    pending.result(timeout=5)
            if fault == "api-before-commit":
                # The production worker parent watchdog must reap its work even
                # though the API's own timeout owner was forcibly terminated.
                until = time.monotonic() + 5
                while True:
                    try:
                        os.kill(process["pid"], 0)
                    except ProcessLookupError:
                        break
                    assert time.monotonic() < until, "Orphan rule worker still alive"
                    time.sleep(0.05)
    assert initial_record(repo) == first
    with server(settings, tmp_path, tls=tls) as origin:
        pair.origin = origin
        if committed:
            receipt = pair.request(
                pair.path + "/receipts/" + request["commandId"],
                access=pair.access[actor],
            )
            assert receipt["matchSeq"] == 1
        else:
            with pytest.raises(HTTPError) as missing:
                pair.request(
                    pair.path + "/receipts/" + request["commandId"],
                    access=pair.access[actor],
                )
            assert missing.value.code == 404
        before = pair.view(actor)
        assert before["matchSeq"] == int(committed)
        accepted = pair.submit(actor, request)
        assert accepted["matchSeq"] == 1
        assert pair.submit(actor, request) == accepted
        for i in range(2):
            current = pair.view(i)
            private(current, f"PLAYER{i+1}")
            assert current["matchSeq"] == current["stateVersion"] == 1
            with pair.stream(i, views[i]["streamCursor"]) as ws:
                assert pushed(ws) == current
            page = pair.request(pair.path + "/frames", access=pair.access[i])
            assert [f["matchSeq"] for f in page["frames"]] == [0, 1]
            assert page["frames"][0] == replay_page([views[i]])["frames"][0]
        assert initial_record(repo) == first
        with repo.db() as db:
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM ptcg_v2.match_receipts"
                ).fetchone()["n"]
                == 1
            )
            assert (
                db.execute("SELECT count(*) AS n FROM ptcg_v2.match_frames").fetchone()[
                    "n"
                ]
                == 4
            )
        report(
            fault,
            {
                "committedBeforeLoss": committed,
                "finalMatchSeq": 1,
                "receipts": 1,
                "visibleFrames": 4,
                "initialRecordUnchanged": True,
            },
        )

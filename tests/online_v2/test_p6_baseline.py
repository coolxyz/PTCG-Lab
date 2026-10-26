"""Fixed synthetic service/SQL workload, not a real-rule capacity claim."""

from dataclasses import replace
import json
import os
from pathlib import Path
import time
import uuid

import pytest
from ptcg_online_server.initialization import Initializer
from ptcg_online_server.lobby import Lobby
from ptcg_online_server.matches import Matches
from ptcg_online_server.repository import Repository
from tests.online_v2.test_lobby import RELEASE, body, prepare
from tests.online_v2.test_matches import GameWorker, command


@pytest.mark.parametrize("reuse", [False, True], ids=["new-connection", "pool"])
def test_fixed_sql_baseline_with_same_durable_outcomes(repo, reuse):
    samples = []
    for _ in range(3):
        settings = replace(
            repo.settings, approved_rule_release_id=RELEASE, db_pool_enabled=reuse
        )
        with Repository(settings) as measured:
            worker = GameWorker()
            lobby = Lobby(measured, worker)
            matches = Matches(measured, worker)
            started = time.perf_counter()
            command_seconds = []
            for _ in range(5):
                a, b, room, _ = prepare(lobby)
                mid = room["matchId"]
                assert Initializer(measured, worker).tick()
                assert matches.promote_one()
                controllers = [uuid.uuid4().hex, uuid.uuid4().hex]
                for token, controller in zip((a, b), controllers):
                    matches.control(
                        token,
                        mid,
                        body(controllerId=controller, expectedControllerEpoch=0),
                    )
                # Ten idle poll cycles for both seats, with their authorized cursor.
                views = [matches.get(token, mid) for token in (a, b)]
                before_idle = measured.metrics.snapshot()["metrics"]["db.sql"]["count"]
                for _ in range(10):
                    for token, view in zip((a, b), views):
                        assert matches.stream(token, mid, view["streamCursor"]) == []
                idle_queries = (
                    measured.metrics.snapshot()["metrics"]["db.sql"]["count"]
                    - before_idle
                )
                for i in range(4):
                    token = (a, b)[i % 2]
                    view = matches.get(token, mid)
                    request = command(view)
                    start = time.perf_counter()
                    receipt = matches.command(token, mid, request, controllers[i % 2])
                    command_seconds.append(time.perf_counter() - start)
                    # Replay the exact accepted command: no second state advance.
                    assert receipt == matches.command(
                        token, mid, request, controllers[i % 2]
                    )
                for token in (a, b):
                    view = matches.get(token, mid)
                    assert (view["matchSeq"], view["done"], view["winner"]) == (
                        4,
                        True,
                        "PLAYER1",
                    )
                    page = matches.frames(token, mid)
                    frames = page["frames"]
                    while page["nextCursor"]:
                        page = matches.frames(token, mid, page["nextCursor"])
                        frames += page["frames"]
                    assert [f["matchSeq"] for f in frames] == [0, 1, 2, 3, 4]
                    opponent = (
                        "PLAYER2" if view["viewerSeat"] == "PLAYER1" else "PLAYER1"
                    )
                    assert "private-" + opponent not in json.dumps(frames)
            samples.append(
                {
                    "seconds": time.perf_counter() - started,
                    "commandSeconds": command_seconds,
                    "idleSqlPer20Polls": idle_queries,
                    "diagnostics": measured.diagnostics(),
                }
            )
            metrics = samples[-1]["diagnostics"]["metrics"]
            assert (
                metrics["db.rejected"]["count"] == 0
                if "db.rejected" in metrics
                else True
            )
            assert metrics["db.connect"]["count"] == (
                1 if reuse else metrics["db.transaction"]["count"]
            )
    output = os.environ.get("PTCG_V2_REPORT_DIRECTORY")
    if output:
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        (path / ("p6a-pool.json" if reuse else "p6a-new-connection.json")).write_text(
            json.dumps(
                {
                    "schema": 1,
                    "workload": "synthetic-service-sql-v1",
                    "repeat": 3,
                    "matchesPerSample": 5,
                    "commandsPerMatch": 4,
                    "idlePollsPerMatch": 20,
                    "duplicateRetriesPerMatch": 4,
                    "realRuleCapacityMeasured": False,
                    "samples": samples,
                },
                indent=2,
            )
            + "\n"
        )

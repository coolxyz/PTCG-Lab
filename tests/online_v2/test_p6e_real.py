# ruff: noqa: F811
"""Real process admission and committed-cache behavior across SQL failure."""

from dataclasses import replace
import json
import pytest
from ptcg_online_client.transport import Server, Transport
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.matches import Matches
from ptcg_online_server.repository import Repository
from ptcg_online_server.worker import RuleWorker
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_real_matches import choose
from tests.online_v2.test_process import server
from tests.online_v2.native_pair import NativePair, pushed


def test_production_process_full_stream_slots_leave_http_command_capacity(
    real, tmp_path
):
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    # Launch the actual __main__ path (not the test Uvicorn TLS launcher).
    with server(
        settings,
        tmp_path,
        extra_env={
            "PTCG_ONLINE_V2_MAX_INFLIGHT": "1",
            "PTCG_ONLINE_V2_MAX_STREAMS": "2",
        },
    ) as origin:
        pair = NativePair(origin, None)
        pair.prepare(inputs)
        transport = Transport(Server(origin, allow_loopback_http=True))
        with transport.stream(pair.access[0]) as first:
            with transport.stream(pair.access[1]) as second:
                for ws in (first, second):
                    ws.send(
                        json.dumps(
                            dict(
                                protocolVersion="2.0",
                                matchId=pair.mid,
                                streamCursor=None,
                            )
                        )
                    )
                views = [pushed(ws) for ws in (first, second)]
                actor = next(i for i, v in enumerate(views) if v["decision"])
                result = pair.submit(
                    actor, choose(views[actor], "http-beside-two-streams")
                )
                assert result["accepted"] and result["matchSeq"] == 1
                assert [pushed(ws)["matchSeq"] for ws in (first, second)] == [1, 1]


@pytest.mark.parametrize("failure", ["rollback", "ack_lost"])
def test_real_cached_candidate_sql_failure_or_lost_ack_never_reapplies(
    real, tmp_path, monkeypatch, failure
):
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    with server(settings, tmp_path) as origin:
        pair = NativePair(origin, None)
        pair.prepare(inputs)
    worker = RuleWorker(replace(settings, worker_count=2))
    try:
        assert worker.probe()
        matches = Matches(repo, worker)
        views = [matches.get(t, pair.mid) for t in pair.access]
        actor = next(i for i, v in enumerate(views) if v["decision"])
        request = choose(views[actor], "sql-boundary")
        with repo.db() as db:
            original = db.execute(
                "SELECT recovery FROM ptcg_v2.matches WHERE id=%s", (pair.mid,)
            ).fetchone()["recovery"]
        worker.project_for_match(original, pair.mid)
        assert worker.work_class("match:" + pair.mid) == "hot"
        if failure == "rollback":
            remember = matches._remember

            def fail(db, *args):
                db.execute("SELECT 1 / 0")

            monkeypatch.setattr(matches, "_remember", fail)
            with pytest.raises(ServiceError, match="INTERNAL_ERROR"):
                matches.command(
                    pair.access[actor], pair.mid, request, pair.controllers[actor]
                )
            assert worker.work_class("match:" + pair.mid) == "cold"
            with repo.db() as db:
                assert (
                    db.execute(
                        "SELECT recovery FROM ptcg_v2.matches WHERE id=%s", (pair.mid,)
                    ).fetchone()["recovery"]
                    == original
                )
            monkeypatch.setattr(matches, "_remember", remember)
        else:
            finish = worker.finish_candidate

            def lose_ack(mid, candidate, committed):
                assert committed
                worker.slots[candidate.ticket[0]].close()
                return finish(mid, candidate, committed)

            monkeypatch.setattr(worker, "finish_candidate", lose_ack)
        receipt = matches.command(
            pair.access[actor], pair.mid, request, pair.controllers[actor]
        )
        count = worker.metrics.snapshot()["metrics"]["rule.compute"]["count"]
        assert (
            matches.command(
                pair.access[actor], pair.mid, request, pair.controllers[actor]
            )
            == receipt
        )
        assert worker.metrics.snapshot()["metrics"]["rule.compute"]["count"] == count
        with repo.db() as db:
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM ptcg_v2.match_receipts WHERE match_id=%s",
                    (pair.mid,),
                ).fetchone()["n"]
                == 1
            )
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM ptcg_v2.match_frames WHERE match_id=%s",
                    (pair.mid,),
                ).fetchone()["n"]
                == 4
            )
        assert matches.get(pair.access[actor], pair.mid)["matchSeq"] == 1
    finally:
        worker.close()

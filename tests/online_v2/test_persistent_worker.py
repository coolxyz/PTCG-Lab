# ruff: noqa: F811
"""Persistent-worker equivalence and generation/cache failure boundaries."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace

import pytest
from ptcg_online_server.config import Settings
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.lanes import lane_for
from ptcg_online_server.metrics import Metrics
from ptcg_online_server.worker import RuleWorker
from ptcg_online_server.worker_runtime import RuleRuntime
from ptcg_online_server.worker_transport import WorkerSlot
from ptcg_protocol.projection import snapshot, core_command
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_real_matches import choose
from tests.online_v2.test_matches import game, command  # noqa: F401


def next_command(candidate, step, name):
    seat, view = next((s, v) for s, v in candidate["views"].items() if v["decision"])
    wire = snapshot(
        view,
        match_id="a" * 32,
        rule_release_id=candidate["recovery"]["implementation"],
        viewer_seat=seat,
        match_seq=step,
        controller_epoch=1,
        stream_cursor="b" * 32,
    )
    return seat, core_command(
        choose(wire, name), trusted_view=wire, authenticated_seat=seat
    )


def match_ids():
    lanes = [
        [str(i) for i in range(100) if lane_for("match:" + str(i), 2) == n]
        for n in (0, 1)
    ]
    return lanes[0][:2] + lanes[1][:1]


def test_persistent_interleaved_matches_equal_cold_restore_and_uncommitted_candidates(
    real,
):
    settings, inputs = real
    cold = RuleWorker(replace(settings, worker_persistent=False))
    hot = RuleWorker(replace(settings, worker_count=2, worker_cache_entries=4))
    try:
        assert cold.probe() and hot.probe()
        as_of = hot.validate(inputs["deck1"])["asOf"]
        records = [
            cold.initialize(
                {
                    **inputs,
                    "rule_release_id": inputs["release"],
                    "as_of": as_of,
                    "seed": seed,
                }
            )
            for seed in (1341, 1341, 1342)
        ]
        assert records[0] == records[1]
        ids = match_ids()
        pids = [s.process.pid for s in hot.slots]
        compared = 0
        for step in range(5):
            for i, mid in enumerate(ids):
                before = hot.project_for_match(records[i], mid)
                assert before == cold.project(records[i])
                seat, request = next_command(before, step, "step-" + str(step))
                expected = cold.command(records[i], seat, request)
                candidate = hot.command_for_match(records[i], seat, request, mid)
                assert candidate == expected and "_cacheTicket" not in candidate
                assert hot.finish_candidate(mid, candidate, True)
                records[i] = candidate["recovery"]
                compared += 2
            assert records[0] == records[1]  # Same seed is independent of interleaving.
        assert [s.process.pid for s in hot.slots] == pids
        assert hot.metrics.snapshot()["metrics"]["rule.load"]["count"] == 2
        assert hot.metrics.snapshot()["metrics"]["rule.restore"]["count"] == 3
        assert cold.metrics.snapshot()["metrics"]["rule.restore"]["count"] == 30

        before = hot.project_for_match(records[0], ids[0])
        seat, request = next_command(before, 5, "not-committed")
        lost = hot.command_for_match(records[0], seat, request, ids[0])
        # No acknowledgement: retry uses SQL's original baseline, not the candidate.
        retry = hot.command_for_match(records[0], seat, request, ids[0])
        assert retry == lost
        assert not hot.finish_candidate(ids[0], lost, True)
        assert hot.finish_candidate(ids[0], retry, True)
        # Explicit rollback invalidates the candidate and restores the old record.
        before = hot.project_for_match(records[1], ids[1])
        seat, request = next_command(before, 5, "rolled-back")
        rejected = hot.command_for_match(records[1], seat, request, ids[1])
        assert not hot.finish_candidate(ids[1], rejected, False)
        assert hot.project_for_match(records[1], ids[1]) == cold.project(records[1])

        output = os.environ.get("PTCG_V2_REPORT_DIRECTORY")
        if output:
            Path(output, "p6d-workers.json").write_text(
                json.dumps(
                    dict(
                        workload="persistent-interleaved-v1",
                        workers=2,
                        matches=3,
                        commandsPerMatch=5,
                        comparedViews=compared,
                        hotLoads=2,
                        hotRestores=3,
                        coldRestores=30,
                        sameSeedEquivalent=True,
                        uncommittedCandidateDiscarded=True,
                        capacityMeasured=False,
                    ),
                    indent=2,
                )
                + "\n"
            )
    finally:
        cold.close()
        hot.close()


def test_worker_death_recovery_is_per_lane_and_cold_budget_preserves_hot_work(real):
    settings, inputs = real
    worker = RuleWorker(replace(settings, worker_count=2))
    try:
        assert worker.probe()
        as_of = worker.validate(inputs["deck1"])["asOf"]
        record = worker.initialize(
            {
                **inputs,
                "rule_release_id": inputs["release"],
                "as_of": as_of,
                "seed": 1341,
            }
        )
        ids = match_ids()
        first = worker.project_for_match(record, ids[0])
        second = worker.project_for_match(record, ids[2])
        generation = worker.slots[0].generation
        other_pid = worker.slots[1].process.pid
        worker.cold.acquire()
        try:
            assert worker.project_for_match(record, ids[2]) == second
            with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
                worker.project_for_match(record, ids[1])
        finally:
            worker.cold.release()
        seat, request = next_command(first, 0, "old-generation")
        uncommitted = worker.command_for_match(record, seat, request, ids[0])
        os.kill(worker.slots[0].process.pid, signal.SIGKILL)
        worker.slots[0].process.wait(timeout=2)
        assert worker.project_for_match(record, ids[2]) == second
        assert worker.slots[1].process.pid == other_pid
        assert worker.project_for_match(record, ids[0]) == first
        assert worker.slots[0].generation != generation
        assert not worker.finish_candidate(ids[0], uncommitted, True)
        # A forged historical record cannot hit the cache just by reusing stateDigest.
        changed = deepcopy(record)
        changed["config"]["seed"] += 1
        with pytest.raises(ServiceError, match="RULE_RELEASE_UNAVAILABLE"):
            worker.project_for_match(changed, ids[0])
        assert worker.project_for_match(record, ids[2]) == second
        assert worker.project_for_match(record, ids[0]) == first
    finally:
        worker.close()


def test_worker_rss_watermark_recycles_process_after_response(real, monkeypatch):
    settings, _ = real
    worker = RuleWorker(settings)
    original = worker.request

    def tiny_watermark(operation, payload):
        request = original(operation, payload)
        request["limits"]["rss"] = (
            1  # Private fixture forces the production recycle path.
        )
        return request

    monkeypatch.setattr(worker, "request", tiny_watermark)
    try:
        assert worker.probe() and worker.slots[0].process is None
        assert worker.probe() and worker.slots[0].process is None
        assert worker.metrics.snapshot()["metrics"]["worker.recycled"]["count"] == 2
    finally:
        worker.close()


def test_runtime_lru_idle_record_budget_and_restore_deadline():
    runtime = RuleRuntime.__new__(RuleRuntime)
    from collections import OrderedDict

    runtime.cache, runtime.pending = OrderedDict(), None
    runtime.limits = dict(entries=1, bytes=1024, idle=0.02, restore=0.02)
    match = SimpleNamespace(_adapter=SimpleNamespace(done=False))
    metrics = Metrics()
    runtime.remember("a", {"v": 1}, match, metrics)
    runtime.remember("b", {"v": 2}, match, metrics)
    assert list(runtime.cache) == ["b"]
    runtime.remember("huge", {"v": "x" * 2048}, match, metrics)
    assert not runtime.cache
    runtime.remember("idle", {"v": 1}, match, metrics)
    time.sleep(0.03)
    runtime.prune(metrics)
    assert not runtime.cache
    match._adapter.done = True
    runtime.remember("done", {"v": 1}, match, metrics)
    assert not runtime.cache
    runtime.factory = SimpleNamespace(restore_match=lambda _: time.sleep(0.2))
    with pytest.raises(TimeoutError):
        runtime.restore(dict(recovery={"v": 1}), metrics)


@pytest.mark.parametrize("kind", ["generation", "requestId", "oversize", "timeout"])
def test_pipe_limits_and_stale_generation_results_are_rejected(kind):
    script = """import json,sys,time
for line in sys.stdin:
 r=json.loads(line)
 mode=sys.argv[1]
 if mode=='timeout': time.sleep(5)
 elif mode=='oversize': sys.stdout.write('x'*(8*1024*1024+2)); sys.stdout.flush()
 else:
  result=dict(generation=r['generation'],requestId=r['requestId'],result={})
  result[mode]='wrong'
  print(json.dumps(result),flush=True)
"""
    slot = WorkerSlot([sys.executable, "-u", "-c", script, kind], {})
    try:
        with pytest.raises((ValueError, TimeoutError)):
            slot.exchange({}, 0.2 if kind == "timeout" else 3)
        child = slot.process
    finally:
        slot.close()
    assert child.poll() is not None and slot.generation is None


@pytest.mark.parametrize("rollback", [False, True])
def test_cache_confirmation_observes_sql_commit_or_rollback(
    game, monkeypatch, rollback
):
    service, _, tokens, mid, controllers = game
    before = service.get(tokens[0], mid)
    confirmed = []

    def finish(match_id, candidate, committed):
        with service.repo.db() as db:
            row = db.execute(
                "SELECT match_seq,recovery FROM ptcg_v2.matches WHERE id=%s",
                (match_id,),
            ).fetchone()
        assert row["match_seq"] == (0 if rollback else 1)
        assert committed is (not rollback)
        if committed:
            assert row["recovery"] == candidate["recovery"]
        confirmed.append(committed)

    monkeypatch.setattr(service.worker, "finish_candidate", finish, raising=False)
    if rollback:

        def fail(*args):
            raise ServiceError("INTERNAL_ERROR", 503)

        monkeypatch.setattr(service, "_remember", fail)
        with pytest.raises(ServiceError):
            service.command(tokens[0], mid, command(before), controllers[0])
    else:
        assert service.command(tokens[0], mid, command(before), controllers[0])[
            "accepted"
        ]
    assert confirmed == [not rollback]


@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_persistent", 1),
        ("worker_count", 0),
        ("worker_count", 3),
        ("worker_cold_limit", 2),
        ("worker_cache_entries", -1),
        ("worker_cache_entries", 65),
        ("worker_cache_record_bytes", 0),
        ("worker_rss_mb", 63),
        ("worker_cache_idle", float("nan")),
        ("worker_restore_seconds", 21),
        ("worker_compute_seconds", True),
    ],
)
def test_worker_settings_bound_resources(field, value):
    with pytest.raises(ValueError, match="INVALID_WORKER_BUDGET"):
        Settings(
            "postgresql://unused", "test", "https://example.test", **{field: value}
        )


def test_real_restore_timeout_reaps_only_its_worker_generation(real):
    settings, inputs = real
    worker = RuleWorker(
        replace(settings, worker_count=2, worker_restore_seconds=0.000001)
    )
    try:
        assert worker.probe()
        as_of = worker.validate(inputs["deck1"])["asOf"]
        record = worker.initialize(
            {
                **inputs,
                "rule_release_id": inputs["release"],
                "as_of": as_of,
                "seed": 1341,
            }
        )
        child = worker.slots[0].process
        other = worker.slots[1].process
        with pytest.raises(ServiceError, match="RULE_RELEASE_UNAVAILABLE"):
            worker.project_for_match(record, match_ids()[0])
        assert child.poll() is not None and worker.slots[0].generation is None
        assert worker.call("probe", _lane=1)["release"] == inputs["release"]
        assert worker.slots[1].process is other and other.poll() is None
    finally:
        worker.close()

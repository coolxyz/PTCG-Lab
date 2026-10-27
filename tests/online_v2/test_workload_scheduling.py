"""Bounded weighted classes, per-lane serial work and global cold admission."""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.lanes import lane_for
from ptcg_online_server.metrics import Metrics
from ptcg_online_server.scheduler import FairScheduler
from ptcg_online_server.worker import RuleWorker
from tests.online_v2.test_scheduler import config, until


def test_weighted_hot_cold_and_maintenance_rotate_without_starvation():
    async def run():
        queue = FairScheduler(
            config(),
            Metrics(),
            classifier=lambda g: "cold" if g.startswith("C") else "hot",
        )
        queue.start()
        gate, order = asyncio.Event(), []

        async def work(name):
            order.append(name)
            if name == "block":
                await gate.wait()

        def enqueue(name, maintenance=False):
            return asyncio.create_task(
                queue.submit(
                    name, name, name, name, lambda: work(name), maintenance=maintenance
                )
            )

        jobs = [enqueue("block")]
        try:
            await until(lambda: order == ["block"])
            jobs += [enqueue(n) for n in ("H1", "H2", "H3", "C1", "C2")]
            jobs.append(enqueue("M1", True))
            await until(lambda: queue.waiters == 7)
            gate.set()
            await asyncio.gather(*jobs)
            assert order == ["block", "H1", "C1", "M1", "H2", "H3", "C2"]
            assert queue.cold_running == queue.maintenance_jobs == 0
            assert all(v == 0 for v in queue.diagnostics().values())
            return order
        finally:
            gate.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            await queue.stop()

    order = asyncio.run(run())
    output = os.environ.get("PTCG_V2_REPORT_DIRECTORY")
    if output:
        Path(output, "p6e-scheduling.json").write_text(
            json.dumps(
                dict(
                    workload="weighted-budget-v1",
                    hotWeight=2,
                    coldWeight=1,
                    maintenanceWeight=1,
                    order=order,
                    drained=True,
                    capacityMeasured=False,
                ),
                indent=2,
            )
            + "\n"
        )


def lane_group(prefix, lane):
    return next(
        prefix + str(i) for i in range(100) if lane_for(prefix + str(i), 2) == lane
    )


@pytest.mark.parametrize("expire", [False, True])
def test_cold_permit_waits_without_blocking_other_lane_hot_work(expire):
    async def run():
        settings = config(worker_count=2, command_queue_seconds=0.05 if expire else 2)
        queue = FairScheduler(
            settings,
            Metrics(),
            classifier=lambda g: "cold" if g.startswith("C") else "hot",
        )
        queue.start()
        gate, order = asyncio.Event(), []
        c0, c1, h1 = lane_group("C0", 0), lane_group("C1", 1), lane_group("H", 1)

        async def work(name):
            order.append(name)
            assert queue.cold_running <= 1
            if name == c0:
                await gate.wait()

        def submit(name):
            return asyncio.create_task(
                queue.submit(name, name, name, name, lambda: work(name))
            )

        first = submit(c0)
        await until(lambda: order == [c0])
        cold, hot = submit(c1), submit(h1)
        try:
            await hot
            assert order == [c0, h1] and not cold.done()
            if expire:
                with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
                    await cold
                gate.set()
                await first
                assert order == [c0, h1]
            else:
                gate.set()
                await asyncio.gather(first, cold)
                assert order == [c0, h1, c1]
            assert queue.cold_running == 0
        finally:
            gate.set()
            await asyncio.gather(first, cold, hot, return_exceptions=True)
            await queue.stop()
        assert all(v == 0 for v in queue.diagnostics().values())

    asyncio.run(run())


def test_maintenance_queue_is_bounded_and_weighted_rollback_keeps_fifo():
    async def run():
        queue = FairScheduler(
            config(maintenance_queue=1, worker_weighted_enabled=False),
            Metrics(),
            classifier=lambda _: "cold",
        )
        queue.start()
        gate, order = asyncio.Event(), []
        first = asyncio.create_task(queue.submit("A", "A", "a", "a", gate.wait))
        await until(lambda: queue.running_group == "A")

        async def work(name):
            order.append(name)

        pending = asyncio.create_task(
            queue.submit("M", "M", "m", "m", lambda: work("M"), maintenance=True)
        )
        await until(lambda: queue.maintenance_jobs == 1)
        with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
            await queue.submit(
                "M2", "M2", "m2", "m2", lambda: work("wrong"), maintenance=True
            )
        foreground = asyncio.create_task(
            queue.submit("H", "H", "h", "h", lambda: work("H"))
        )
        await until(lambda: queue.waiters == 3)
        gate.set()
        await asyncio.gather(first, pending, foreground)
        await queue.stop()
        assert order == ["M", "H"] and queue.maintenance_jobs == 0

    asyncio.run(run())


def test_worker_cache_hints_are_bounded_generation_scoped_and_advisory():
    worker = RuleWorker(config(worker_cache_entries=1))
    try:
        worker.slots[0].generation = "generation-one"
        assert worker.work_class("match:A") == "cold"
        worker.cache_hint(0, "A", present=True)
        assert worker.work_class("match:A") == "hot"
        worker.cache_hint(0, "B", present=True)
        assert worker.work_class("match:A") == "cold"  # LRU bound, no rule state.
        worker.slots[0].generation = "generation-two"
        assert worker.work_class("match:B") == "cold"
        worker.cache_hint(0, "B", present=True)
        worker.hints[0]["B"] = (
            "generation-two",
            time.monotonic() - worker.settings.worker_cache_idle - 1,
        )
        assert worker.work_class("match:B") == "cold"
        assert worker.work_class("deck:ordinary") == "hot"
    finally:
        worker.close()


def test_stale_hot_hint_is_cleared_even_when_cold_permit_is_full(monkeypatch):
    worker = RuleWorker(config())
    worker.slots[0].generation = "synthetic-generation"
    worker.cache_hint(0, "A", present=True)
    monkeypatch.setattr(worker.slots[0], "exchange", lambda *args: {"cached": False})
    worker.cold.acquire()
    try:
        with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
            worker.project_for_match({}, "A")
        assert worker.work_class("match:A") == "cold"
    finally:
        worker.cold.release()
        worker.close()

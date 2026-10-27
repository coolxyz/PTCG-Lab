"""Deterministic scheduling tests: barriers, bounded callers, no real-rule SLA."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest
from ptcg_online_server.config import Settings
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.metrics import Metrics
from ptcg_online_server.scheduler import FairScheduler


def config(**values):
    return Settings(
        "postgresql://unused", "queue-test", "https://example.test", **values
    )


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def test_scheduler_fifo_round_robin_and_duplicate_merge():
    async def run():
        queue = FairScheduler(config(command_queue_per_match=4), Metrics())
        queue.start()
        gate = asyncio.Event()
        order = []

        async def operation(name):
            order.append(name)
            if name == "A0":
                await gate.wait()
            return name

        def submit(name):
            return asyncio.create_task(
                queue.submit(name[0], name[0], name, name, lambda: operation(name))
            )

        tasks = [submit("A0")]
        try:
            await until(lambda: order == ["A0"])
            tasks += [submit(n) for n in ("A1", "A2", "B0", "B1", "C0", "C1")]
            tasks += [submit("A0") for _ in range(3)]
            await until(lambda: queue.waiters == 10)
            assert queue.diagnostics()["jobs"] == 7
            with pytest.raises(ServiceError, match="IDEMPOTENCY_CONFLICT"):
                await queue.submit(
                    "A", "A", "A0", "different", lambda: operation("wrong")
                )
            gate.set()
            results = await asyncio.gather(*tasks)
            assert results.count("A0") == 4
            assert order == ["A0", "B0", "C0", "A1", "B1", "C1", "A2"]
            assert all(v == 0 for v in queue.diagnostics().values())
            assert queue.metrics.snapshot()["metrics"]["scheduler.merged"]["count"] == 3
            return dict(
                workload="fair-command-queue-v1",
                groups=3,
                uniqueJobs=7,
                mergedRetries=3,
                executions=len(order),
                order=order,
                drained=True,
                capacityMeasured=False,
            )
        finally:
            gate.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await queue.stop()

    result = asyncio.run(run())
    directory = os.environ.get("PTCG_V2_REPORT_DIRECTORY")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / "p6c-scheduler.json").write_text(json.dumps(result, indent=2) + "\n")


@pytest.mark.parametrize(
    "budget", ["total", "per_match", "per_user", "waiters", "duplicates"]
)
def test_scheduler_admission_limits_are_independent(budget):
    async def run():
        queue = FairScheduler(config(**{"command_queue_" + budget: 1}), Metrics())
        queue.start()
        gate = asyncio.Event()
        first = asyncio.create_task(queue.submit("A", "U", "first", "first", gate.wait))
        try:
            await until(lambda: queue.running_group == "A")
            group = "A" if budget in {"per_match", "duplicates"} else "B"
            user = "U" if budget in {"per_user", "duplicates"} else "V"
            key = "first" if budget == "duplicates" else "second"
            with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED") as error:
                await queue.submit(group, user, key, key, gate.wait)
            assert error.value.retryable and error.value.status == 503
            assert queue.diagnostics()["jobs"] == 1
        finally:
            gate.set()
            await first
            await queue.stop()

    asyncio.run(run())


def test_scheduler_expiry_cancellation_shutdown_and_failure_release_budgets():
    async def run():
        queue = FairScheduler(config(command_queue_seconds=0.04), Metrics())
        queue.start()
        gate = asyncio.Event()
        ran = []

        async def first_op():
            await gate.wait()
            ran.append("committed")

        async def unwanted():
            ran.append("unexpected")

        first = asyncio.create_task(queue.submit("A", "U", "0", "0", first_op))
        await until(lambda: queue.running_group == "A")
        second = asyncio.create_task(queue.submit("B", "V", "1", "1", unwanted))
        with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
            await second
        cancelled = asyncio.create_task(queue.submit("B", "V", "2", "2", unwanted))
        await until(lambda: queue.diagnostics()["queued"] == 1)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        assert queue.diagnostics()["queued"] == 0
        # Losing the last executing caller must not interrupt its eventual commit.
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert queue.diagnostics()["running"] == 1
        pending = asyncio.create_task(queue.submit("B", "V", "3", "3", unwanted))
        await until(lambda: queue.diagnostics()["queued"] == 1)
        closing = asyncio.create_task(queue.stop())
        with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
            await pending
        assert not closing.done()
        gate.set()
        await closing
        assert ran == ["committed"] and all(
            v == 0 for v in queue.diagnostics().values()
        )
        with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
            await queue.submit("A", "U", "4", "4", unwanted)

        other = FairScheduler(config(), Metrics())
        other.start()

        async def broken():
            raise ValueError("private-canary")

        try:
            with pytest.raises(ServiceError, match="INTERNAL_ERROR") as error:
                await other.submit("A", "U", "0", "0", broken)
            assert "private-canary" not in str(error.value)
            assert await other.submit("B", "V", "1", "1", gate.wait) is True
            assert all(v == 0 for v in other.diagnostics().values())
        finally:
            await other.stop()

    asyncio.run(run())


def test_scheduler_rollback_switch_bypasses_only_queue():
    async def run():
        queue = FairScheduler(config(command_queue_enabled=False), Metrics())
        queue.start()

        async def op():
            return "executed"

        assert await queue.submit("A", "U", "0", "0", op) == "executed"
        assert queue.task is None and not queue.jobs
        await queue.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", 1),
        ("total", 0),
        ("total", 257),
        ("per_match", 17),
        ("per_user", 33),
        ("waiters", 513),
        ("duplicates", 33),
        ("seconds", 0),
        ("seconds", 11),
        ("seconds", float("nan")),
        ("seconds", True),
    ],
)
def test_scheduler_settings_reject_invalid_or_unbounded_limits(field, value):
    with pytest.raises(ValueError, match="INVALID_COMMAND_QUEUE_BUDGET"):
        replace(config(), **{"command_queue_" + field: value})


def test_scheduler_environment_has_explicit_rollback(monkeypatch):
    monkeypatch.delenv("PTCG_ONLINE_V2_DATABASE_URL_FILE", raising=False)
    for key, value in dict(
        DATABASE_URL="postgresql://unused",
        SERVER_ID="test",
        PUBLIC_ORIGIN="https://example.test",
        COMMAND_QUEUE_ENABLED="0",
    ).items():
        monkeypatch.setenv("PTCG_ONLINE_V2_" + key, value)
    assert not Settings.from_env().command_queue_enabled
    monkeypatch.setenv("PTCG_ONLINE_V2_COMMAND_QUEUE_ENABLED", "maybe")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_two_worker_lanes_run_independently_but_each_match_stays_serial():
    from ptcg_online_server.lanes import current_lane, lane_for

    async def run():
        queue = FairScheduler(config(worker_count=2), Metrics())
        queue.start()
        groups = [
            next(str(i) for i in range(100) if lane_for(str(i), 2) == n) for n in (0, 1)
        ]
        gate = asyncio.Event()
        order = []

        async def op(name, blocked=False):
            order.append((name, current_lane.get()))
            if blocked:
                await gate.wait()
            return name

        first = asyncio.create_task(
            queue.submit(groups[0], "a", "0", "0", lambda: op("A0", True))
        )
        try:
            await until(lambda: len(order) == 1)
            second = asyncio.create_task(
                queue.submit(groups[0], "a", "1", "1", lambda: op("A1"))
            )
            other = asyncio.create_task(
                queue.submit(groups[1], "b", "0", "0", lambda: op("B0"))
            )
            assert await asyncio.wait_for(other, 1) == "B0"
            assert order == [("A0", 0), ("B0", 1)] and not second.done()
            gate.set()
            await asyncio.gather(first, second)
            assert order[-1] == ("A1", 0)
        finally:
            gate.set()
            await queue.stop()

    asyncio.run(run())

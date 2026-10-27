"""Single-process bounded FIFO mailboxes, round-robin groups, bounded rule lanes.

No database handles or rule state live here. The operation reauthorizes, reads
its committed baseline and fences the commit. PostgreSQL owns idempotency.
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging

from .errors import ServiceError
from .lanes import current_lane, lane_for


@dataclass(eq=False)
class Job:
    key: tuple
    digest: str
    operation: object = field(repr=False)
    future: asyncio.Future = field(repr=False)
    created: float
    waiters: int = 0
    running: bool = False
    maintenance: bool = False
    timer: object = None


class FairScheduler:
    def __init__(self, settings, metrics, *, classifier=None):
        self.settings, self.metrics = settings, metrics
        self.jobs, self.queues, self.users, self.groups = {}, {}, {}, {}
        self.ready = deque()
        self.running_groups = set()
        self.lanes = settings.worker_count if settings.worker_persistent else 1
        self.waiters = 0
        self.wakeups = [asyncio.Event() for _ in range(self.lanes)]
        self.task = None
        self.closed = False
        self.classifier = classifier
        self.weighted = settings.worker_weighted_enabled and classifier is not None
        self.cold_running = 0
        self.maintenance_jobs = 0
        self.wheel = ["hot"] * settings.worker_hot_weight + ["cold", "maintenance"]
        self.turns = [0] * self.lanes

    def start(self):
        if self.settings.command_queue_enabled:
            self.task = asyncio.gather(*(self.run(lane) for lane in range(self.lanes)))

    @property
    def running_group(self):
        return next(iter(self.running_groups), None)

    def ready_group(self, group):
        self.ready.append(group)
        self.wakeups[lane_for(group, self.lanes)].set()

    def diagnostics(self):
        return dict(
            jobs=len(self.jobs),
            queued=sum(map(len, self.queues.values())),
            running=len(self.running_groups),
            waiters=self.waiters,
            groups=len(self.groups),
            users=len(self.users),
        )

    def reject(self):
        self.metrics.observe("scheduler.rejected")
        raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)

    async def submit(
        self, group, user, request_id, digest, operation, *, maintenance=False
    ):
        if self.closed:
            self.reject()
        if not self.settings.command_queue_enabled:
            return await operation()
        if self.task is None:
            self.reject()
        key = (group, user, request_id)
        job = self.jobs.get(key)
        if job is not None and job.digest != digest:
            raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
        if self.waiters >= self.settings.command_queue_waiters:
            self.reject()
        if job is not None:
            if job.waiters >= self.settings.command_queue_duplicates:
                self.reject()
            self.metrics.observe("scheduler.merged")
        else:
            if (
                (
                    maintenance
                    and self.maintenance_jobs >= self.settings.maintenance_queue
                )
                or len(self.jobs) >= self.settings.command_queue_total
                or self.groups.get(group, 0) >= self.settings.command_queue_per_match
                or self.users.get(user, 0) >= self.settings.command_queue_per_user
            ):
                self.reject()
            loop = asyncio.get_running_loop()
            job = Job(
                key,
                digest,
                operation,
                loop.create_future(),
                loop.time(),
                maintenance=maintenance,
            )
            self.maintenance_jobs += int(maintenance)
            # A disconnected last caller must not leave an unobserved exception.
            job.future.add_done_callback(
                lambda f: None if f.cancelled() else f.exception()
            )
            self.jobs[key] = job
            self.users[user] = self.users.get(user, 0) + 1
            self.groups[group] = self.groups.get(group, 0) + 1
            queue = self.queues.setdefault(group, deque())
            if not queue and group not in self.running_groups:
                self.ready_group(group)
            queue.append(job)
            job.timer = loop.call_later(
                self.settings.command_queue_seconds, self.expire, job
            )
            self.metrics.observe("scheduler.accepted")

        self.waiters += 1
        job.waiters += 1
        try:
            # Caller cancellation cannot cancel an executing operation or commit.
            return await asyncio.shield(job.future)
        finally:
            self.waiters -= 1
            job.waiters -= 1
            if job.waiters == 0 and not job.running and self.jobs.get(key) is job:
                self.remove_queued(job)
                self.metrics.observe("scheduler.cancelled")
                job.future.cancel()

    def release(self, job):
        self.jobs.pop(job.key)
        self.maintenance_jobs -= int(job.maintenance)
        group, user, _ = job.key
        for counts, key in ((self.groups, group), (self.users, user)):
            counts[key] -= 1
            if not counts[key]:
                del counts[key]
        job.timer.cancel()

    def remove_queued(self, job):
        group = job.key[0]
        queue = self.queues[group]
        queue.remove(job)
        if not queue:
            del self.queues[group]
            if group in self.ready:
                self.ready.remove(group)
        self.release(job)
        for wakeup in self.wakeups:
            wakeup.set()

    def expire(self, job):
        if self.jobs.get(job.key) is job and not job.running:
            self.remove_queued(job)
            self.metrics.observe("scheduler.expired")
            job.future.set_exception(
                ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
            )

    def classification(self, group):
        job = self.queues[group][0]
        cold = self.classifier(group) == "cold" if self.classifier else False
        return ("maintenance" if job.maintenance else "cold" if cold else "hot"), cold

    def choose(self, lane):
        if not self.weighted:
            return next(
                (g for g in self.ready if lane_for(g, self.lanes) == lane), None
            ), False
        busy_lanes = {lane_for(g, self.lanes) for g in self.running_groups}
        classified = {g: self.classification(g) for g in self.ready}
        # Global FIFO cold admission across idle lanes; a busy lane cannot reserve
        # the permit. Classification is only a hint; the worker verifies the record.
        cold_ready = [
            g
            for g in self.ready
            if classified[g][1] and lane_for(g, self.lanes) not in busy_lanes
        ]
        available = max(0, self.settings.worker_cold_limit - self.cold_running)
        eligible = [
            g
            for g in self.ready
            if lane_for(g, self.lanes) == lane
            and (not classified[g][1] or g in cold_ready[:available])
        ]
        for offset in range(len(self.wheel)):
            index = (self.turns[lane] + offset) % len(self.wheel)
            group = next(
                (g for g in eligible if classified[g][0] == self.wheel[index]), None
            )
            if group is not None:
                self.turns[lane] = (index + 1) % len(self.wheel)
                return group, classified[group][1]
        return None, False

    async def run(self, lane):
        while not self.closed:
            await self.wakeups[lane].wait()
            group, cold = self.choose(lane)
            if group is None:
                self.wakeups[lane].clear()
                continue
            self.ready.remove(group)
            job = self.queues[group].popleft()
            if not self.queues[group]:
                del self.queues[group]
            # Timers can be delayed by event-loop load. Check again before work.
            if (
                asyncio.get_running_loop().time() - job.created
                >= self.settings.command_queue_seconds
            ):
                self.release(job)
                self.metrics.observe("scheduler.expired")
                job.future.set_exception(
                    ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
                )
                if group in self.queues:
                    self.ready_group(group)
                for wakeup in self.wakeups:
                    wakeup.set()
                continue
            self.running_groups.add(group)
            self.cold_running += int(cold)
            if self.weighted:
                self.metrics.observe(
                    "scheduler."
                    + ("maintenance" if job.maintenance else "cold" if cold else "hot")
                )
            context = current_lane.set(lane)
            job.running = True
            job.timer.cancel()
            self.metrics.observe(
                "scheduler.wait", asyncio.get_running_loop().time() - job.created
            )
            try:
                value = await job.operation()
            except ServiceError as error:
                job.future.set_exception(error)
            except (Exception, asyncio.CancelledError):
                logging.getLogger("ptcg_online_server").error(
                    "scheduler_operation_failed"
                )
                job.future.set_exception(
                    ServiceError("INTERNAL_ERROR", 503, retryable=True)
                )
            else:
                job.future.set_result(value)
            finally:
                self.release(job)
                self.running_groups.remove(group)
                self.cold_running -= int(cold)
                for wakeup in self.wakeups:
                    wakeup.set()
                current_lane.reset(context)
                # A busy match rejoins at the back, after already-ready matches.
                if group in self.queues:
                    self.ready_group(group)

    async def stop(self):
        self.closed = True
        for job in list(self.jobs.values()):
            if not job.running:
                self.remove_queued(job)
                job.future.set_exception(
                    ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
                )
        for wakeup in self.wakeups:
            wakeup.set()
        if self.task:
            # Drain bounded rule operations; never release their lanes early.
            await asyncio.shield(self.task)

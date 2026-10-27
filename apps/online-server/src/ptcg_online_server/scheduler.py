"""Single-process bounded FIFO mailboxes, round-robin groups, one rule lane.

No database handles or rule state live here. The operation reauthorizes, reads
its committed baseline and fences the commit. PostgreSQL owns idempotency.
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging

from .errors import ServiceError


@dataclass(eq=False)
class Job:
    key: tuple
    digest: str
    operation: object = field(repr=False)
    future: asyncio.Future = field(repr=False)
    created: float
    waiters: int = 0
    running: bool = False
    timer: object = None


class FairScheduler:
    def __init__(self, settings, metrics):
        self.settings, self.metrics = settings, metrics
        self.jobs, self.queues, self.users, self.groups = {}, {}, {}, {}
        self.ready = deque()
        self.running_group = None
        self.waiters = 0
        self.wakeup = asyncio.Event()
        self.task = None
        self.closed = False

    def start(self):
        if self.settings.command_queue_enabled:
            self.task = asyncio.create_task(self.run())

    def diagnostics(self):
        return dict(
            jobs=len(self.jobs),
            queued=sum(map(len, self.queues.values())),
            running=int(self.running_group is not None),
            waiters=self.waiters,
            groups=len(self.groups),
            users=len(self.users),
        )

    def reject(self):
        self.metrics.observe("scheduler.rejected")
        raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)

    async def submit(self, group, user, request_id, digest, operation):
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
                len(self.jobs) >= self.settings.command_queue_total
                or self.groups.get(group, 0) >= self.settings.command_queue_per_match
                or self.users.get(user, 0) >= self.settings.command_queue_per_user
            ):
                self.reject()
            loop = asyncio.get_running_loop()
            job = Job(key, digest, operation, loop.create_future(), loop.time())
            # A disconnected last caller must not leave an unobserved exception.
            job.future.add_done_callback(
                lambda f: None if f.cancelled() else f.exception()
            )
            self.jobs[key] = job
            self.users[user] = self.users.get(user, 0) + 1
            self.groups[group] = self.groups.get(group, 0) + 1
            queue = self.queues.setdefault(group, deque())
            if not queue and self.running_group != group:
                self.ready.append(group)
            queue.append(job)
            job.timer = loop.call_later(
                self.settings.command_queue_seconds, self.expire, job
            )
            self.metrics.observe("scheduler.accepted")
            self.wakeup.set()
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

    def expire(self, job):
        if self.jobs.get(job.key) is job and not job.running:
            self.remove_queued(job)
            self.metrics.observe("scheduler.expired")
            job.future.set_exception(
                ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
            )

    async def run(self):
        while not self.closed:
            await self.wakeup.wait()
            if not self.ready:
                self.wakeup.clear()
                continue
            group = self.ready.popleft()
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
                    self.ready.append(group)
                continue
            self.running_group = group
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
                self.running_group = None
                # A busy match rejoins at the back, after already-ready matches.
                if group in self.queues:
                    self.ready.append(group)

    async def stop(self):
        self.closed = True
        for job in list(self.jobs.values()):
            if not job.running:
                self.remove_queued(job)
                job.future.set_exception(
                    ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
                )
        self.wakeup.set()
        if self.task:
            # Drain the one bounded rule operation; never release its lane early.
            await asyncio.shield(self.task)

"""Bounded per-process wakeups; PostgreSQL remains the durable source of truth."""

import asyncio
from dataclasses import dataclass, field
import threading
import uuid

from starlette.concurrency import run_in_threadpool

from .auth import token_digest
from .errors import ServiceError


@dataclass(eq=False)
class Subscription:
    key: str
    digest: str = field(repr=False)
    match_id: str
    cursor: str | None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    error: ServiceError | None = None


class StreamHub:
    """At most max_streams metadata records and one coalescing event per peer.

    No private frame queues, global watermarks, auth cache, or dedicated DB
    connection. Local commits are hints; a one-second sweep covers missed hints,
    other writers, token rotation, expiry and revocation. Socket sends never run
    in the dispatcher, so a stalled peer cannot hold back other matches.
    """

    def __init__(self, matches):
        self.matches = matches
        self.entries = {}
        self.wakeup = asyncio.Event()
        self.task = self.loop = None
        self.stopped = False
        self.lock = threading.Lock()
        self.scheduled = False

    def start(self):
        self.loop = asyncio.get_running_loop()
        self.matches.repo.stream_wakeup = self.notify
        self.task = asyncio.create_task(self.run())

    def notify(self):
        # Called from SQL worker threads: coalesce before scheduling on the loop.
        with self.lock:
            if self.stopped or self.loop is None or self.scheduled:
                return
            self.scheduled = True
            self.loop.call_soon_threadsafe(self._wake)

    def _wake(self):
        with self.lock:
            self.scheduled = False
        self.wakeup.set()

    def add(self, token, match_id, cursor):
        if self.stopped or len(self.entries) >= self.matches.repo.settings.max_streams:
            raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
        entry = Subscription(
            uuid.uuid4().hex, token_digest(token, "atk"), match_id, cursor
        )
        self.entries[entry.key] = entry
        entry.changed.set()  # Initial authorization and cursor validation.
        self.wakeup.set()
        return entry

    def remove(self, entry):
        self.entries.pop(entry.key, None)

    async def scan(self):
        entries = list(self.entries.values())
        if not entries:
            return
        try:
            rows = await run_in_threadpool(
                self.matches.stream_heads,
                [
                    dict(key=e.key, digest=e.digest, match_id=e.match_id)
                    for e in entries
                ],
            )
        except ServiceError as error:
            # Fail closed on database failure. Clients resume from durable cursors.
            for entry in entries:
                entry.error = error
                entry.changed.set()
            return
        for row in rows:
            entry = self.entries.get(row["key"])
            if entry is None:
                continue
            if row["error"]:
                entry.error = ServiceError(
                    row["error"], 401 if row["error"] == "UNAUTHORIZED" else 403
                )
            if entry.error or entry.cursor != row["cursor"]:
                entry.changed.set()

    async def run(self):
        while not self.stopped:
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=1)
            except TimeoutError:
                pass
            self.wakeup.clear()
            if not self.stopped:
                await self.scan()
                # Limit hint storms to 20 sweeps/s; periodic catch-up is retained.
                await asyncio.sleep(0.05)

    async def stop(self):
        with self.lock:
            self.stopped = True
        self.matches.repo.stream_wakeup = None
        self.wakeup.set()
        if self.task:
            await self.task
        for entry in self.entries.values():
            entry.error = ServiceError("INTERNAL_ERROR", 503, retryable=True)
            entry.changed.set()
        self.entries.clear()

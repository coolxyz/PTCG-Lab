"""Test-image-only ASGI timing. No diagnostic HTTP route or production patch."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import time
import uuid
from threading import Lock
import uvicorn
from ptcg_online_server.api import create_app
from ptcg_online_server.config import Settings

try:
    from .measure import Histogram, LIMITS
except ImportError:
    from measure import Histogram, LIMITS

PHASES = ("setup", "warmup", "steady", "fault", "cleanup")
CURRENT = ContextVar("p7_command_spans", default=None)


class Spans:
    """Numeric request-local timings only; context follows threadpool dispatch."""

    def __init__(self):
        self.lock, self.values = Lock(), {}

    def add(self, name, seconds):
        with self.lock:
            self.values[name] = self.values.get(name, 0.0) + seconds


def record(name, seconds):
    spans = CURRENT.get()
    if spans is not None:
        spans.add(name, seconds)


def instrument(app):
    """Decorate this disposable app instance, leaving installed P6 sources intact."""
    repo, scheduler = app.state.repository, app.state.scheduler
    observe = repo.metrics.observe

    def metric(name, seconds=0):
        record(name, seconds)
        return observe(name, seconds)

    repo.metrics.observe = metric
    execute = repo.connection_class.execute
    transaction = repo.connection_class.transaction

    def sql(connection, query, *args, **kwargs):
        # Query text is inspected locally, never saved or exported.
        category = (
            "advisory"
            if "pg_advisory" in str(query)
            else "locked_read"
            if "FOR UPDATE" in str(query)
            else "write"
            if str(query).lstrip().startswith(("INSERT", "UPDATE", "DELETE"))
            else "read"
        )
        start = time.perf_counter()
        try:
            return execute(connection, query, *args, **kwargs)
        finally:
            record("sql." + category, time.perf_counter() - start)

    @contextmanager
    def tx(connection, *args, **kwargs):
        context = transaction(connection, *args, **kwargs)
        value = context.__enter__()
        try:
            yield value
        except BaseException as error:
            start = time.perf_counter()
            try:
                if not context.__exit__(type(error), error, error.__traceback__):
                    raise
            finally:
                record("db.rollback_exit", time.perf_counter() - start)
        else:
            start = time.perf_counter()
            try:
                context.__exit__(None, None, None)
            finally:
                record("db.commit_exit", time.perf_counter() - start)

    repo.connection_class.execute = sql
    repo.connection_class.transaction = tx
    submit = scheduler.submit

    async def scheduled(group, user, request_id, digest, operation, **kwargs):
        spans = CURRENT.get()
        started = time.perf_counter()
        operation_seconds = 0

        async def correlated():
            nonlocal operation_seconds
            token = CURRENT.set(spans)
            operation_start = time.perf_counter()
            try:
                return await operation()
            finally:
                operation_seconds += time.perf_counter() - operation_start
                CURRENT.reset(token)

        try:
            return await submit(group, user, request_id, digest, correlated, **kwargs)
        finally:
            record(
                "scheduled.queue",
                max(0, time.perf_counter() - started - operation_seconds),
            )

    scheduler.submit = scheduled


class Observed:
    def __init__(self, app):
        self.app = app
        self.directory = Path("/measure")
        self.epoch = uuid.uuid4().hex
        self.histograms = {p: Histogram() for p in PHASES}
        self.responses = {p: {} for p in PHASES}
        self.ws_bytes = 0
        self.task = None
        self.stop = asyncio.Event()
        self.spans = {p: {} for p in PHASES}
        self.slowest = {p: [] for p in PHASES}
        self.loop_lag = Histogram()
        instrument(app)

    def phase(self):
        try:
            value = (self.directory / "phase").read_text().strip()
            return value if value in PHASES else "setup"
        except OSError:
            return "setup"

    def write(self):
        value = dict(
            schema=1,
            epoch=self.epoch,
            time=time.time(),
            bucketUpperSeconds=[*LIMITS, None],
            phases={p: self.histograms[p].snapshot() for p in PHASES},
            responses=self.responses,
            websocketJsonBytes=self.ws_bytes,
            metrics=self.app.state.repository.metrics.snapshot(),
            scheduler=self.app.state.scheduler.diagnostics(),
            streams=self.app.state.streams.active,
            commandSpans={
                p: {k: h.snapshot() for k, h in v.items()}
                for p, v in self.spans.items()
            },
            slowestCommands=self.slowest,
            eventLoopLag=self.loop_lag.snapshot(),
            apiCpuStat=dict(
                line.split()
                for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines()
            )
            if Path("/sys/fs/cgroup/cpu.stat").exists()
            else {},
        )
        target = self.directory / (self.epoch + ".json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        temporary.replace(target)

    async def collect(self):
        while not self.stop.is_set():
            self.write()
            try:
                started = asyncio.get_running_loop().time()
                await asyncio.wait_for(self.stop.wait(), 1)
            except TimeoutError:
                self.loop_lag.observe(
                    max(0, asyncio.get_running_loop().time() - started - 1)
                )
        self.write()

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        phase, started = self.phase(), time.perf_counter()
        command = (
            kind == "http"
            and scope["method"] == "POST"
            and scope["path"].endswith("/commands")
        )
        spans = Spans() if command else None
        token = CURRENT.set(spans)

        async def measured(message):
            if kind == "lifespan":
                if message["type"] == "lifespan.startup.complete":
                    self.task = asyncio.create_task(self.collect())
                elif message["type"] == "lifespan.shutdown.complete":
                    self.stop.set()
                    if self.task:
                        await self.task
            if command and message["type"] == "http.response.start":
                code = str(message["status"])
                self.responses[phase][code] = self.responses[phase].get(code, 0) + 1
                if message["status"] == 200:
                    elapsed = time.perf_counter() - started
                    self.histograms[phase].observe(elapsed)
                    values = dict(spans.values)
                    for name, seconds in values.items():
                        self.spans[phase].setdefault(name, Histogram()).observe(seconds)
                    rows = self.slowest[phase]
                    rows.append(dict(seconds=elapsed, spans=values))
                    rows.sort(key=lambda r: r["seconds"], reverse=True)
                    del rows[32:]
            if kind == "websocket" and message["type"] == "websocket.send":
                self.ws_bytes += len(
                    message.get("bytes") or message.get("text", "").encode()
                )
            await send(message)

        try:
            await self.app(scope, receive, measured)
        finally:
            CURRENT.reset(token)


def main():
    if "PTCG_P7_FIXTURE_SEED" in os.environ:
        import ptcg_online_server.lobby as lobby
        from fixture import install

        install(lobby, int(os.environ["PTCG_P7_FIXTURE_SEED"]))
    settings = Settings.from_env()
    # TLS is in the measured API process; there is no unbudgeted proxy.
    uvicorn.run(
        Observed(create_app(settings)),
        host="0.0.0.0",
        port=8784,
        ssl_certfile="/tls/server.pem",
        ssl_keyfile="/tls/server-key.pem",
        proxy_headers=False,
        access_log=False,
        server_header=False,
        limit_concurrency=settings.max_inflight
        + settings.max_streams
        + settings.connection_reserve,
        timeout_keep_alive=5,
        ws="websockets",
        ws_max_size=16384,
        ws_max_queue=2,
        ws_per_message_deflate=False,
    )


if __name__ == "__main__":
    main()

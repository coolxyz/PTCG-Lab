"""Test-image-only ASGI timing. No diagnostic HTTP route or production patch."""

import asyncio
import json
from pathlib import Path
import time
import uuid
import uvicorn
from ptcg_online_server.api import create_app
from ptcg_online_server.config import Settings

try:
    from .measure import Histogram, LIMITS
except ImportError:
    from measure import Histogram, LIMITS

PHASES = ("setup", "warmup", "steady", "fault", "cleanup")


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
        )
        target = self.directory / (self.epoch + ".json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        temporary.replace(target)

    async def collect(self):
        while not self.stop.is_set():
            self.write()
            try:
                await asyncio.wait_for(self.stop.wait(), 1)
            except TimeoutError:
                pass
        self.write()

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        phase, started = self.phase(), time.perf_counter()
        command = (
            kind == "http"
            and scope["method"] == "POST"
            and scope["path"].endswith("/commands")
        )

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
                    self.histograms[phase].observe(time.perf_counter() - started)
            if kind == "websocket" and message["type"] == "websocket.send":
                self.ws_bytes += len(
                    message.get("bytes") or message.get("text", "").encode()
                )
            await send(message)

        await self.app(scope, receive, measured)


def main():
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

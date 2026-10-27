"""Bounded native WebSockets with durable cursor recovery and centralized wakeups."""

import asyncio
from contextlib import suppress
from fastapi import WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from .contracts import decode
from .errors import ServiceError
from .stream_hub import StreamHub


class Streams:
    def __init__(self, settings, matches):
        self.settings, self.matches = settings, matches
        self.active = 0
        self.hub = StreamHub(matches)

    async def send(self, ws, value):
        # A slow consumer gets disconnected and resumes from its last cursor.
        await asyncio.wait_for(ws.send_json(value), timeout=3)

    async def close(self, ws, code):
        try:
            await ws.close(code=code)
        except (RuntimeError, WebSocketDisconnect):
            # The peer may disappear between a failed send and our close frame.
            pass

    async def request(self, ws, kind, timeout):
        message = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect()
        text = message.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 4096:
            raise ServiceError("INVALID_PAYLOAD", 400)
        return decode(kind, text.encode("utf-8"))

    async def serve(self, ws):
        if self.active >= self.settings.max_streams:
            return await self.close(ws, 1013)
        self.active += 1
        accepted = False
        try:
            items = ws.scope.get("headers", [])
            headers = dict(items)
            if any(
                sum(k == key for k, _ in items) != 1
                for key in (b"host", b"authorization")
            ):
                raise ServiceError("UNAUTHORIZED", 401)
            if (
                headers[b"host"].decode("latin1").lower() != self.settings.host
                or b"origin" in headers
                or b"cookie" in headers
                or ws.scope.get("query_string")
            ):
                raise ServiceError("FORBIDDEN", 403)
            parts = headers[b"authorization"].decode("latin1").split(" ")
            if len(parts) != 2 or parts[0].lower() != "bearer":
                raise ServiceError("UNAUTHORIZED", 401)
            token = parts[1]
            await run_in_threadpool(
                self.matches.repo.rate, (ws.scope.get("client") or ("unknown",))[0]
            )
            await run_in_threadpool(self.matches.sessions.authenticate, token)
            await ws.accept()
            accepted = True
            subscription = await self.request(ws, "MatchSubscribe", 10)
            if self.settings.stream_hub_enabled:
                await self.centralized(ws, token, subscription)
            else:
                await self.polling(ws, token, subscription)
        except ServiceError as error:
            if accepted:
                try:
                    await self.send(ws, error.payload())
                except (TimeoutError, RuntimeError, WebSocketDisconnect):
                    pass
            await self.close(ws, 1008 if error.status < 500 else 1013)
        except (TimeoutError, RuntimeError, WebSocketDisconnect):
            await self.close(ws, 1013)
        finally:
            self.active -= 1

    async def polling(self, ws, token, subscription):
        cursor = subscription["streamCursor"]
        ticks = 0
        while True:
            start = asyncio.get_running_loop().time()
            updates = await run_in_threadpool(
                self.matches.stream, token, subscription["matchId"], cursor
            )
            for event in updates:
                await self.send(
                    ws,
                    {
                        "protocolVersion": "2.0",
                        "type": event["kind"],
                        "snapshot": event["snapshot"],
                    },
                )
                cursor = event["snapshot"]["streamCursor"]
            ticks += 1
            if ticks % 15 == 0:
                await self.send(
                    ws,
                    {
                        "protocolVersion": "2.0",
                        "type": "heartbeat",
                        "streamCursor": cursor,
                    },
                )
            try:
                await self.request(ws, "MatchPing", 1)
            except TimeoutError:
                pass
            await asyncio.sleep(max(0, 1 - (asyncio.get_running_loop().time() - start)))

    async def centralized(self, ws, token, subscription):
        entry = self.hub.add(
            token, subscription["matchId"], subscription["streamCursor"]
        )

        async def receive_pings():
            while True:
                try:
                    await self.request(ws, "MatchPing", 30)
                    # Preserve the old one-ping-per-second read budget. Uvicorn
                    # bounds its receive queue; a flood must not busy-loop here.
                    await asyncio.sleep(1)
                except TimeoutError:
                    pass

        receiver = asyncio.create_task(receive_pings())
        heartbeat = asyncio.get_running_loop().time() + 15
        try:
            while True:
                waiter = asyncio.create_task(entry.changed.wait())
                try:
                    await asyncio.wait(
                        (receiver, waiter),
                        timeout=max(0, heartbeat - asyncio.get_running_loop().time()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    waiter.cancel()
                    with suppress(asyncio.CancelledError):
                        await waiter
                if receiver.done():
                    receiver.result()
                if entry.error:
                    raise entry.error
                if entry.changed.is_set():
                    # Clear before reading: a commit during the read sets it again.
                    entry.changed.clear()
                    updates = await run_in_threadpool(
                        self.matches.stream, token, entry.match_id, entry.cursor
                    )
                    for event in updates:
                        if entry.error:
                            raise entry.error
                        await self.send(
                            ws,
                            {
                                "protocolVersion": "2.0",
                                "type": event["kind"],
                                "snapshot": event["snapshot"],
                            },
                        )
                        entry.cursor = event["snapshot"]["streamCursor"]
                    # Drain bounded pages promptly, without an unbounded queue.
                    if len(updates) == 8:
                        entry.changed.set()
                if asyncio.get_running_loop().time() >= heartbeat:
                    await self.send(
                        ws,
                        {
                            "protocolVersion": "2.0",
                            "type": "heartbeat",
                            "streamCursor": entry.cursor,
                        },
                    )
                    heartbeat = asyncio.get_running_loop().time() + 15
        finally:
            self.hub.remove(entry)
            receiver.cancel()
            with suppress(
                asyncio.CancelledError, ServiceError, RuntimeError, WebSocketDisconnect
            ):
                await receiver

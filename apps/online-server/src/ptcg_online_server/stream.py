"""Bounded native WebSocket stream backed by the durable, member-scoped outbox.

P4 baseline polls once per second; P6 can replace wakeups without changing cursor
or transaction semantics. This is not the measured high-concurrency scheduler.
"""

import asyncio
from fastapi import WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from .contracts import decode
from .errors import ServiceError


class Streams:
    def __init__(self, settings, matches):
        self.settings, self.matches = settings, matches
        self.active = 0

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
                await asyncio.sleep(
                    max(0, 1 - (asyncio.get_running_loop().time() - start))
                )
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

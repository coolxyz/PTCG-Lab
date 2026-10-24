"""Optional ASGI bridge; the native shell supplies origin, profiles and window IPC."""

import asyncio
from contextlib import asynccontextmanager
import re
from urllib.parse import urlsplit
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from ptcg_online_client.contracts import ClientError, unpack
from .bridge import BridgeError

PREFIX = "/api/desktop/online/v1"
MAX_BODY = 32768


def response(value, status=200):
    return JSONResponse(
        value,
        status,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
        },
    )


class BridgeAPI:
    def __init__(self, owner, origin):
        target = urlsplit(origin)
        if (
            target.scheme != "http"
            or target.hostname != "127.0.0.1"
            or not target.port
            or target.port == 80
            or origin != f"http://127.0.0.1:{target.port}"
        ):
            raise ValueError(
                "Bridge origin must be explicit loopback HTTP with a nondefault port"
            )
        self.owner, self.origin, self.host = owner, origin, target.netloc
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return
        try:
            token = self.guard(scope)
            if self.active >= 4:
                raise BridgeError("BRIDGE_BUSY", 503)
            self.active += 1
            try:
                body = await asyncio.wait_for(self.read(receive), timeout=5)
                path = scope["path"]
                if path == PREFIX + "/profiles":
                    if body != {}:
                        raise BridgeError("INVALID_REQUEST")
                    result = self.owner.list_profiles(token)
                elif path == PREFIX + "/close-window":
                    if body != {}:
                        raise BridgeError("INVALID_REQUEST")
                    self.owner.revoke_window(token)
                    result = {"closed": True}
                else:
                    match = re.fullmatch(
                        re.escape(PREFIX)
                        + r"/profiles/([a-z0-9-]{1,32})/([a-z-]{1,32})",
                        path,
                    )
                    if match is None:
                        raise BridgeError("UNKNOWN_ROUTE", 404)
                    result = await run_in_threadpool(
                        self.owner.call, token, *match.groups(), body
                    )
                reply = response({"result": result})
            finally:
                self.active -= 1
        except BridgeError as error:
            reply = response(
                {
                    "error": {
                        "code": error.code,
                        "retryable": error.status in (409, 429, 503),
                    }
                },
                error.status,
            )
        except ClientError as error:
            # Only fixed codes and typed public state; never repr/str(exception).
            code = (
                error.code
                if re.fullmatch(r"[A-Z_]{1,64}", error.code)
                else "REMOTE_FAILED"
            )
            reply = response(
                {"error": {"code": code, "retryable": error.retryable}},
                error.status if 400 <= error.status <= 599 else 409,
            )
        except asyncio.TimeoutError:
            reply = response(
                {"error": {"code": "REQUEST_TIMEOUT", "retryable": False}}, 408
            )
        except Exception:
            reply = response(
                {"error": {"code": "BRIDGE_FAILED", "retryable": False}}, 500
            )
        await reply(scope, receive, send)

    def guard(self, scope):
        raw = scope.get("headers", [])
        names = [key.lower() for key, _ in raw]
        unique = (
            b"host",
            b"origin",
            b"x-ptcg-bridge",
            b"content-type",
            b"content-length",
            b"sec-fetch-site",
        )
        if any(names.count(key) > 1 for key in unique):
            raise BridgeError("LOCAL_AUTH_REQUIRED", 403)
        headers = dict((key.lower(), value.decode("latin1")) for key, value in raw)
        if (
            scope.get("client", (None,))[0] not in ("127.0.0.1", "::1")
            or scope.get("scheme") != "http"
            or headers.get(b"host") != self.host
            or headers.get(b"origin") != self.origin
            or headers.get(b"sec-fetch-site", "same-origin") != "same-origin"
            or any(
                key in headers
                for key in (
                    b"authorization",
                    b"cookie",
                    b"forwarded",
                    b"x-forwarded-host",
                    b"x-forwarded-for",
                    b"x-forwarded-proto",
                )
            )
            or scope.get("query_string")
            or b"%" in scope.get("raw_path", b"")
        ):
            raise BridgeError("LOCAL_AUTH_REQUIRED", 403)
        token = headers.get(b"x-ptcg-bridge", "")
        self.owner.authorize(token)
        if scope["method"] != "POST":
            raise BridgeError("METHOD_NOT_ALLOWED", 405)
        if (
            headers.get(b"content-type") != "application/json"
            or b"content-encoding" in headers
        ):
            raise BridgeError("INVALID_CONTENT_TYPE", 415)
        length = headers.get(b"content-length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_BODY):
            raise BridgeError("PAYLOAD_TOO_LARGE", 413)
        return token

    async def read(self, receive):
        data = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise BridgeError("REQUEST_ABORTED")
            data.extend(message.get("body", b""))
            if len(data) > MAX_BODY:
                raise BridgeError("PAYLOAD_TOO_LARGE", 413)
            if not message.get("more_body", False):
                break
        try:
            body = unpack(data)
        except ClientError:
            raise BridgeError("INVALID_REQUEST") from None
        if not isinstance(body, dict):
            raise BridgeError("INVALID_REQUEST")
        if "requestId" in body and (
            not isinstance(body["requestId"], str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", body["requestId"])
        ):
            raise BridgeError("INVALID_REQUEST")
        return body


def create_app(owner, *, origin):
    """No auto-routing into the existing app; mount only under a trusted native host."""

    @asynccontextmanager
    async def lifespan(app):
        yield
        await run_in_threadpool(owner.close)

    # Starlette supplies ASGI lifespan only; no docs, CORS, token minting or static UI.
    app = Starlette(lifespan=lifespan)
    app.mount("/", BridgeAPI(owner, origin))
    return app

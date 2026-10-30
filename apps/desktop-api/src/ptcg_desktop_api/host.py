"""Trusted local launch: one-use file POST, HttpOnly local session, per-page capability."""

import asyncio
from contextlib import asynccontextmanager
import html
from pathlib import Path
import re
import secrets
from threading import RLock
import time
from urllib.parse import parse_qs
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response, RedirectResponse, FileResponse
from .api import BridgeAPI, PREFIX, response
from .bridge import BridgeError
from .platform_files import private_directory


class Host:
    def __init__(self, owner, origin, build, resources, launch_dir):
        self.owner, self.origin, self.resources = owner, origin, resources
        self.api = BridgeAPI(owner, origin)
        self.build, self.launch_dir = Path(build).resolve(), Path(launch_dir)
        private_directory(self.launch_dir)
        self.tickets, self.sessions = {}, {}
        self.lock = RLock()
        self.image_slots = 0
        self.active = 0
        self.product_mode = False
        self.codes = {}

    def activation_code(self):
        """Native terminal only. HTTP can consume a code but cannot mint one."""
        with self.lock:
            self._prune()
            if len(self.codes) >= 8:
                raise BridgeError("LAUNCH_LIMIT", 429)
            code = secrets.token_hex(16)
            self.codes[code] = time.monotonic() + 300
            return code

    def native_session(self):
        """Private parent pipe only; there is deliberately no HTTP minting route."""
        with self.lock:
            self._prune()
            if len(self.sessions) >= 8:
                raise BridgeError("LOCAL_BUSY", 503)
            key = secrets.token_urlsafe(32)
            self.sessions[key] = {
                "expires": time.monotonic() + 8 * 3600,
                "windows": {},
            }
            return key

    def launch(self):
        with self.lock:
            self._prune()
            if len(self.tickets) >= 8:
                raise BridgeError("LAUNCH_LIMIT", 429)
            ticket = secrets.token_urlsafe(32)
            path = self.launch_dir / (secrets.token_hex(16) + ".html")
            with path.open("x") as out:
                path.chmod(0o600)
                out.write(
                    '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>启动网络对战</title><form method="post" action="'
                    + html.escape(self.origin + "/launch", quote=True)
                    + '"><input type="hidden" name="ticket" value="'
                    + ticket
                    + '"><button>打开网络对战</button></form><script>document.forms[0].submit()</script>'
                )
            self.tickets[ticket] = (time.monotonic() + 120, path)
            return path

    def _prune(self):
        now = time.monotonic()
        self.codes = {k: t for k, t in self.codes.items() if t > now}
        for key, (expires, path) in list(self.tickets.items()):
            if expires < now:
                path.unlink(missing_ok=True)
                del self.tickets[key]
        for key, value in list(self.sessions.items()):
            with self.owner.lock:
                value["windows"] = {
                    k: v for k, v in value["windows"].items() if v in self.owner.windows
                }
            if value["expires"] < now:
                for token in value["windows"].values():
                    self.owner.revoke_window(token)
                del self.sessions[key]

    def close(self):
        with self.lock:
            for _, path in self.tickets.values():
                path.unlink(missing_ok=True)
            self.tickets.clear()
            self.sessions.clear()
            self.codes.clear()
        self.owner.close()

    def base(self, scope):
        names = [k for k, _ in scope.get("headers", [])]
        if any(
            names.count(k) > 1
            for k in (
                b"host",
                b"origin",
                b"cookie",
                b"content-type",
                b"content-length",
                b"sec-fetch-site",
            )
        ):
            raise BridgeError("LOCAL_AUTH_REQUIRED", 403)
        headers = dict(scope["headers"])
        if (
            scope.get("client", (None,))[0] not in ("127.0.0.1", "::1")
            or scope.get("scheme") != "http"
            or headers.get(b"host", b"").decode() != self.api.host
            or scope.get("query_string")
            or b"%" in scope.get("raw_path", b"")
            or any(k.startswith(b"x-forwarded-") or k == b"forwarded" for k in headers)
        ):
            raise BridgeError("LOCAL_AUTH_REQUIRED", 403)
        return headers

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if self.active >= 8:
            await response({"error": {"code": "LOCAL_BUSY", "retryable": True}}, 503)(
                scope, receive, send
            )
            return
        self.active += 1
        try:
            await self.handle(scope, receive, send)
        finally:
            self.active -= 1

    async def handle(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        path = scope["path"]
        if path.startswith(PREFIX):
            await self.api(scope, receive, send)
            return
        try:
            headers = self.base(scope)
            if path == "/launch" and scope["method"] == "POST":
                if (
                    headers.get(b"origin") != b"null"
                    or headers.get(b"content-type")
                    != b"application/x-www-form-urlencoded"
                ):
                    raise BridgeError("LOCAL_AUTH_REQUIRED", 403)

                async def read():
                    data = b""
                    while True:
                        msg = await receive()
                        if msg["type"] != "http.request":
                            raise BridgeError("INVALID_REQUEST")
                        data += msg.get("body", b"")
                        if len(data) > 256:
                            raise BridgeError("PAYLOAD_TOO_LARGE", 413)
                        if not msg.get("more_body"):
                            return data

                body = parse_qs(
                    (await asyncio.wait_for(read(), 5)).decode(), strict_parsing=True
                )
                with self.lock:
                    self._prune()
                    item = (
                        self.tickets.pop(body["ticket"][0], None)
                        if set(body) == {"ticket"} and len(body["ticket"]) == 1
                        else None
                    )
                    if item is None:
                        raise BridgeError("LAUNCH_EXPIRED", 403)
                    item[1].unlink(missing_ok=True)
                    if len(self.sessions) >= 8:
                        raise BridgeError("LOCAL_BUSY", 503)
                    key = secrets.token_urlsafe(32)
                    self.sessions[key] = {
                        "expires": time.monotonic() + 8 * 3600,
                        "windows": {},
                    }
                reply = RedirectResponse("/", status_code=303)
                reply.set_cookie(
                    "ptcg-local",
                    key,
                    httponly=True,
                    samesite="strict",
                    max_age=8 * 3600,
                    path="/",
                )
            elif (
                path == "/authorize" and scope["method"] == "POST" and self.product_mode
            ):
                if (
                    headers.get(b"origin", b"").decode() != self.origin
                    or headers.get(b"sec-fetch-site", b"same-origin") != b"same-origin"
                    or headers.get(b"content-type") != b"application/json"
                ):
                    raise BridgeError("LOCAL_AUTH_REQUIRED", 403)
                body = await asyncio.wait_for(self.api.read(receive), 5)
                if set(body) != {"code"} or not isinstance(body["code"], str):
                    raise BridgeError("INVALID_REQUEST")
                with self.lock:
                    self._prune()
                    if len(self.sessions) >= 8:
                        raise BridgeError("LOCAL_BUSY", 503)
                    if self.codes.pop(body["code"], None) is None:
                        raise BridgeError("LAUNCH_EXPIRED", 403)
                    key = secrets.token_urlsafe(32)
                    self.sessions[key] = {
                        "expires": time.monotonic() + 8 * 3600,
                        "windows": {},
                    }
                reply = response({"authorized": True})
                reply.set_cookie(
                    "ptcg-local",
                    key,
                    httponly=True,
                    samesite="strict",
                    max_age=8 * 3600,
                    path="/",
                )
            elif path == "/session" and scope["method"] == "POST":
                if (
                    headers.get(b"origin", b"").decode() != self.origin
                    or headers.get(b"sec-fetch-site", b"same-origin") != b"same-origin"
                    or headers.get(b"content-type") != b"application/json"
                ):
                    raise BridgeError("LOCAL_AUTH_REQUIRED", 403)
                body = await asyncio.wait_for(self.api.read(receive), 5)
                if (
                    set(body) != {"windowId"}
                    or not isinstance(body["windowId"], str)
                    or not re.fullmatch(r"[a-f0-9-]{36}", body["windowId"])
                ):
                    raise BridgeError("INVALID_REQUEST")
                cookie = headers.get(b"cookie", b"").decode()
                parts = [
                    s.strip().split("=", 1) for s in cookie.split(";") if s.strip()
                ]
                keys = [v for k, v in parts if k == "ptcg-local"]
                with self.lock:
                    self._prune()
                    session = self.sessions.get(keys[0]) if len(keys) == 1 else None
                    if not session:
                        raise BridgeError("LOCAL_AUTH_REQUIRED", 403)
                    windows = session["windows"]
                    if body["windowId"] in windows:
                        self.owner.revoke_window(windows.pop(body["windowId"]))
                    if len(windows) >= 8:
                        raise BridgeError("WINDOW_LIMIT", 429)
                    token = self.owner.issue_window()
                    windows[body["windowId"]] = token
                reply = response({"capability": token})
            elif path == "/catalog" and scope["method"] == "POST":
                self.api.guard(scope)
                body = await asyncio.wait_for(self.api.read(receive), 5)
                if set(body) != {"catalogVersion", "assetVersion"} or any(
                    not isinstance(v, str) or len(v) > 128 for v in body.values()
                ):
                    raise BridgeError("INVALID_REQUEST")
                reply = response(
                    await run_in_threadpool(
                        self.resources.catalog,
                        body["catalogVersion"],
                        body["assetVersion"],
                    )
                )
            elif scope["method"] == "GET":
                if headers.get(b"sec-fetch-site", b"none") not in (
                    b"none",
                    b"same-origin",
                ) and not (
                    path == "/"
                    and headers.get(b"sec-fetch-mode") == b"navigate"
                    and headers.get(b"sec-fetch-dest") == b"document"
                ):
                    raise BridgeError("LOCAL_AUTH_REQUIRED", 403)
                if path.startswith("/resources/"):
                    if (
                        headers.get(b"sec-fetch-site") != b"same-origin"
                        or self.image_slots >= 4
                    ):
                        raise BridgeError("IMAGE_UNAVAILABLE", 404)
                    self.image_slots += 1
                    try:
                        data, mime = await run_in_threadpool(
                            self.resources.image, path.removeprefix("/resources/")
                        )
                    finally:
                        self.image_slots -= 1
                    reply = Response(
                        data,
                        media_type=mime,
                        headers={
                            "Cache-Control": "private, max-age=31536000, immutable",
                            "Cross-Origin-Resource-Policy": "same-origin",
                        },
                    )
                else:
                    relative = "online.html" if path == "/" else path.lstrip("/")
                    if relative != "online.html" and not re.fullmatch(
                        r"assets/[A-Za-z0-9_-]+\.(js|css)", relative
                    ):
                        raise BridgeError("NOT_FOUND", 404)
                    file = (self.build / relative).resolve()
                    if not file.is_relative_to(self.build) or not file.is_file():
                        raise BridgeError("NOT_FOUND", 404)
                    reply = FileResponse(file)
                    reply.headers["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
                    )
            else:
                raise BridgeError("NOT_FOUND", 404)
        except BridgeError as e:
            reply = response({"error": {"code": e.code, "retryable": False}}, e.status)
        except Exception:
            reply = response(
                {"error": {"code": "LOCAL_REQUEST_FAILED", "retryable": False}}, 400
            )
        reply.headers.setdefault("Cache-Control", "no-store")
        reply.headers["Referrer-Policy"] = "no-referrer"
        reply.headers["X-Content-Type-Options"] = "nosniff"
        await reply(scope, receive, send)


def create_host_app(host):
    @asynccontextmanager
    async def lifespan(app):
        yield
        await run_in_threadpool(host.close)

    app = Starlette(lifespan=lifespan)
    app.mount("/", host)
    return app

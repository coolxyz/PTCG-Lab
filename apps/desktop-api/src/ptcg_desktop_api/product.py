"""One ASGI host for the product API, static GUI and native online bridge.

The product injects its application; this package never imports the product's
SQLite, rules or AI modules. Both lifecycles run in the same HTTP process.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from .api import PREFIX


class ProductHost:
    def __init__(self, local_app, online):
        self.local_app, self.online = local_app, online

    def online_route(self, path):
        if path in {"/launch", "/session", "/catalog", "/online", "/online/"}:
            return True
        if path.startswith(PREFIX) or path.startswith("/resources/"):
            return True
        # Serve the separately built online assets at the same origin. A missing
        # asset falls through to the product app; no second HTTP listener/proxy.
        if path.startswith("/assets/"):
            relative = Path(path.lstrip("/"))
            target = (self.online.build / relative).resolve()
            return target.is_relative_to(self.online.build) and target.is_file()
        return False

    def allowed(self, scope):
        headers = scope.get("headers", [])
        names = [k.lower() for k, _ in headers]
        if any(
            names.count(k) > 1
            for k in (
                b"host",
                b"origin",
                b"cookie",
                b"sec-fetch-site",
                b"content-length",
                b"content-type",
            )
        ):
            return False
        h = dict(headers)
        if (
            scope.get("client", (None,))[0] not in ("127.0.0.1", "::1")
            or scope.get("scheme") != "http"
            or h.get(b"host", b"").decode() != self.online.api.host
            or any(k.startswith(b"x-forwarded-") or k == b"forwarded" for k in names)
        ):
            return False
        if scope["method"] not in {"GET", "HEAD"}:
            return (
                h.get(b"origin", b"").decode() == self.online.origin
                and h.get(b"sec-fetch-site", b"same-origin") == b"same-origin"
            )
        return h.get(b"sec-fetch-site", b"none") in {b"none", b"same-origin"} or (
            scope["path"] == "/"
            and h.get(b"sec-fetch-mode") == b"navigate"
            and h.get(b"sec-fetch-dest") == b"document"
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return
        if self.online_route(scope["path"]):
            if scope["path"] in {"/online", "/online/"}:
                scope = {**scope, "path": "/", "raw_path": b"/"}
            await self.online(scope, receive, send)
        elif self.allowed(scope):
            await self.local_app(scope, receive, send)
        else:
            await JSONResponse(
                {"error": {"code": "LOCAL_AUTH_REQUIRED", "retryable": False}},
                status_code=403,
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )(scope, receive, send)


def create_product_app(local_app, online):
    """Own both shutdowns, including failed local startup; no remote connection."""

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with local_app.router.lifespan_context(local_app) as state:
                yield state
        finally:
            await run_in_threadpool(online.close)

    app = Starlette(lifespan=lifespan)
    app.state.local_app = local_app
    app.state.online = online
    app.mount("/", ProductHost(local_app, online))
    return app

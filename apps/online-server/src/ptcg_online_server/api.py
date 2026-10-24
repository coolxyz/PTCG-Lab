"""Native-client API only: no GUI, cookies, catalogue, practice or AI routes."""

import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from ptcg_protocol.validation import validate

from .auth import Sessions
from .config import Settings
from .contracts import checked, decode
from .errors import ServiceError
from .repository import Repository
from .rules import RuleStatus

PREFIX = "/api/online/v2"
MAX_BODY = 16384


def response(error):
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if error.status == 429:
        headers["Retry-After"] = "60"
    if error.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(error.payload(), status_code=error.status, headers=headers)


class Boundary:
    """Bound the streaming body before parsing. No browser or URL credentials."""

    def __init__(self, app, settings, repository):
        self.app, self.settings, self.repo = app, settings, repository
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            # Stream authentication/subscriptions are introduced with P4-C.
            return await send({"type": "websocket.close", "code": 1008})
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                headers += [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                message = {**message, "headers": headers}
            await send(message)

        if self.inflight >= self.settings.max_inflight:
            return await response(
                ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
            )(scope, receive, safe_send)
        self.inflight += 1
        try:
            items = scope.get("headers", [])
            headers = dict(items)
            for key in (b"host", b"authorization", b"content-type", b"content-length"):
                if sum(k == key for k, _ in items) > 1:
                    raise ServiceError("INVALID_PAYLOAD", 400)
            if (
                headers.get(b"host", b"").decode("latin1").lower() != self.settings.host
                or b"origin" in headers
                or b"cookie" in headers
            ):
                raise ServiceError("FORBIDDEN", 403)
            if scope.get("query_string"):
                raise ServiceError("INVALID_PAYLOAD", 400)
            if b"content-encoding" in headers:
                raise ServiceError("INVALID_PAYLOAD", 415)
            if (
                scope["method"] == "POST"
                and headers.get(b"content-type", b"").split(b";")[0].strip().lower()
                != b"application/json"
            ):
                raise ServiceError("INVALID_PAYLOAD", 415)
            if scope["path"].startswith(PREFIX):
                await run_in_threadpool(
                    self.repo.rate,
                    (scope.get("client") or ("unknown",))[0],
                    creating=scope["path"] == PREFIX + "/sessions"
                    and scope["method"] == "POST",
                )
            body = bytearray()

            async def read():
                while True:
                    part = await receive()
                    if part["type"] == "http.disconnect":
                        return False
                    chunk = part.get("body", b"")
                    if len(body) + len(chunk) > MAX_BODY:
                        raise ServiceError("PAYLOAD_TOO_LARGE", 413)
                    body.extend(chunk)
                    if not part.get("more_body", False):
                        return True

            if not await asyncio.wait_for(read(), timeout=self.settings.body_timeout):
                return
            if scope["method"] != "POST" and body:
                raise ServiceError("INVALID_PAYLOAD", 400)
            consumed = False

            async def buffered():
                nonlocal consumed
                if consumed:
                    return await receive()
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            await self.app(scope, buffered, safe_send)
        except ServiceError as error:
            if not started:
                await response(error)(scope, receive, safe_send)
        except TimeoutError:
            if not started:
                await response(ServiceError("INVALID_PAYLOAD", 408))(
                    scope, receive, safe_send
                )
        except Exception as error:
            logging.getLogger("ptcg_online_server").error(
                "request_failed type=%s", type(error).__name__
            )
            if not started:
                await response(ServiceError("INTERNAL_ERROR", 500))(
                    scope, receive, safe_send
                )
        finally:
            self.inflight -= 1


def bearer(request):
    value = request.headers.get("authorization", "")
    parts = value.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ServiceError("UNAUTHORIZED", 401)
    return parts[1]


def create_app(settings=None):
    settings = settings or Settings.from_env()
    repo = Repository(settings)
    sessions = Sessions(repo)

    @asynccontextmanager
    async def lifespan(app):
        app.state.rules = await run_in_threadpool(RuleStatus, settings)
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(Boundary, settings=settings, repository=repo)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return response(error)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return response(ServiceError("INVALID_PAYLOAD", error.status_code))

    @app.get("/healthz")
    async def health():
        return {"status": "alive"}

    @app.get("/readyz")
    async def ready():
        database = True
        try:
            await run_in_threadpool(repo.healthy)
        except ServiceError:
            database = False
        rules = app.state.rules.failure is None
        return JSONResponse(
            {"ready": database and rules, "database": database, "rules": rules},
            status_code=200 if database and rules else 503,
        )

    @app.get(PREFIX + "/capabilities")
    async def capabilities():
        rules = app.state.rules
        return validate(
            "Capabilities",
            {
                "protocolVersion": "2.0",
                "supportedProtocolVersions": ["2.0"],
                "ruleReleaseIds": [rules.release_id] if rules.release_id else [],
                "resources": {
                    "displayCatalogVersion": rules.catalog_version,
                    "assetManifestVersion": "unavailable",
                },
                "acceptingMatches": False,
                "maxCommandBytes": MAX_BODY,
                "features": [],
            },
        )

    @app.post(PREFIX + "/sessions", status_code=201)
    async def create(request: Request):
        body = decode("Create", await request.body())
        result = await run_in_threadpool(
            sessions.create,
            name=body.get("name", "训练家"),
            device_name=body.get("deviceName", "本地 APP"),
        )
        return checked("Tokens", result)

    @app.post(PREFIX + "/sessions/refresh")
    async def refresh(request: Request):
        body = decode("Refresh", await request.body())
        result = await run_in_threadpool(sessions.refresh, body["refreshToken"])
        return checked("Tokens", result)

    @app.get(PREFIX + "/sessions/current")
    async def current(request: Request):
        return checked(
            "Identity", await run_in_threadpool(sessions.authenticate, bearer(request))
        )

    @app.post(PREFIX + "/sessions/revoke")
    async def revoke(request: Request):
        decode("Revoke", await request.body())
        return checked(
            "Revoked", await run_in_threadpool(sessions.revoke, bearer(request))
        )

    return app

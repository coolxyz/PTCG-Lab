"""Local synchronization API; all repository and runtime paths are server-owned."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, FileResponse
from pydantic import BaseModel, ConfigDict, Field

from packages.sync.common import ROOT, SyncError
from packages.sync.service import SyncService
from packages.sync.display import DisplayResources, MAX_PACK
from packages.collection.domain import RAW, CARDS
from ptcg_engine_provider.catalog import MemoryCatalog
import tempfile


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Repository(Input):
    repository: str = Field(min_length=1, max_length=300)


class ArtworkSettings(Input):
    source: Literal["github-raw", "cdn"] = "github-raw"
    baseUrl: str = Field(default="", max_length=1024)
    capacityMiB: int = Field(default=1024, ge=64, le=8192)


class Start(Input):
    kind: Literal["plan", "sync"] = "sync"
    commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    requestKey: str | None = Field(default=None, min_length=8, max_length=128)


class Activate(Input):
    expectedRelease: str | None


class Rollback(Activate):
    releaseId: str = Field(pattern=r"^[a-f0-9]{64}$")


class ResourceActivation(Input):
    packId: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expectedPack: str | None = Field(pattern=r"^[a-f0-9]{64}$")


class VariantAllocation(Input):
    printingId: str
    condition: str
    variantId: str
    quantity: int = Field(ge=0, le=9999)
    expectedVersion: int


def attach(app, store):
    if os.getenv("PTCG_SYNC_WORKER") == "1":
        return
    # Test or alternate user databases get independent sync state by default.
    default_db = (ROOT / "var/app.sqlite").resolve()
    home = os.getenv("PTCG_SYNC_HOME") or (
        ROOT / ".catalog/sync"
        if Path(store.path).resolve() == default_db
        else Path(store.path).with_suffix(".sync")
    )
    service = SyncService(home, store.path)
    app.state.sync = service
    from packages.sync.artwork import BuiltinArtwork

    artwork = BuiltinArtwork(service.artwork_cache, known=CARDS, synced=service.images)
    app.state.card_media.artwork = artwork
    resources = DisplayResources(
        Path(home) / "display", MemoryCatalog.from_catalog(RAW).catalog_version, CARDS
    )
    service.display_resources = resources
    app.state.card_media.display_resources = resources
    app.state.battles.release_id = service.releases.current
    api = APIRouter(prefix="/api/sync")
    # Waiting image requests must not consume the shared API thread pool;
    # commands and metadata remain responsive while Raw/CDN requests are slow.
    image_slots = anyio.Semaphore(2)
    from packages.sync.variants import Variants

    variants = Variants(service)

    @api.get("/variants")
    def collection_variants():
        return variants.view()

    @api.post("/variants")
    def allocate_variant(body: VariantAllocation):
        return variants.set(
            body.printingId,
            body.condition,
            body.variantId,
            body.quantity,
            body.expectedVersion,
        )

    @api.get("/variants/cards/{printing_id}")
    def card_variants(printing_id: str):
        return variants.view(printing_id)

    @api.post("/variants/holding")
    def save_variant_holding(body: VariantAllocation):
        return variants.set_holding(
            body.printingId,
            body.condition,
            body.variantId,
            body.quantity,
            body.expectedVersion,
        )

    @app.exception_handler(SyncError)
    async def sync_error(request, exc):
        return JSONResponse(
            {"code": exc.code, "message": exc.message}, status_code=exc.status,
            headers={"Retry-After": str(exc.retry_after)} if hasattr(exc, "retry_after") else {},
        )

    @api.get("/artwork/settings")
    def artwork_settings():
        return {**service.artwork_cache.status(), "indexedCards": len(artwork.rows)}

    @api.put("/artwork/settings")
    def configure_artwork(body: ArtworkSettings):
        return service.artwork_cache.configure(body.model_dump())

    @api.get("/artwork/{key}")
    async def artwork_image(key: str):
        async with image_slots:
            data, mime = await anyio.to_thread.run_sync(lambda: artwork.image(key))
        return Response(data, media_type=mime,
            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @api.get("/resources")
    def resource_status():
        return resources.status()

    @api.post("/resources/install", status_code=201)
    async def resource_install(request: Request):
        pin = request.headers.get("x-ptcg-resource-sha256", "")
        from packages.sync.display import digest

        if not digest(pin) or request.headers.get("content-type") != "application/zip":
            raise SyncError(
                "INVALID_RESOURCE_PIN", "请选择 ZIP 并提供发布方的完整 SHA-256"
            )
        # Staging is disk-backed and capped while receiving, never an arbitrary
        # browser filesystem path or an executable upload.
        with tempfile.TemporaryFile() as stream:
            total = 0
            async for block in request.stream():
                total += len(block)
                if total > MAX_PACK:
                    raise SyncError("RESOURCE_CAPACITY", "资源 ZIP 超过 256 MiB", 413)
                stream.write(block)
            stream.seek(0)

            def install():
                with service.jobs.lease():
                    with tempfile.TemporaryDirectory(prefix="ptcg-display-") as folder:
                        target = Path(folder) / "resource.zip"
                        with target.open("wb") as out:
                            import shutil

                            shutil.copyfileobj(stream, out)
                        return resources.install(target, pin)

            return await anyio.to_thread.run_sync(install)

    @api.post("/resources/activate")
    def resource_activate(body: ResourceActivation):
        with service.jobs.lease():
            return resources.activate(body.packId, body.expectedPack)

    @api.get("/resource-images/{pack_id}/{image_hash}")
    def resource_image(pack_id: str, image_hash: str):
        path, mime = resources.image(pack_id, image_hash)
        return FileResponse(
            path,
            media_type=mime,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @api.post("/repository")
    def repository(body: Repository):
        return service.set_repository(body.repository)

    @api.get("/status")
    def status():
        return service.status()

    @api.get("/images/{commit}/{card_id}")
    async def image(commit: str, card_id: str):
        async with image_slots:
            data, mime = await anyio.to_thread.run_sync(lambda: service.images.data(commit, card_id))
        return Response(
            data,
            media_type=mime,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @api.get("/image-status/{commit}")
    def image_status(commit: str):
        service.source.validate_sha(commit)
        return service.images.status(commit)

    @api.post("/check", status_code=202)
    def check():
        return service.start("check")

    @api.post("/jobs", status_code=202)
    def start(body: Start):
        return service.start(body.kind, body.commit, request_key=body.requestKey)

    @api.get("/jobs/{job_id}")
    def get(job_id: str):
        return service.jobs.get(job_id)

    @api.get("/jobs/{job_id}/events")
    def events(job_id: str, after: int = 0):
        return service.jobs.events(job_id, max(0, after))

    @api.get("/jobs/{job_id}/tasks")
    def tasks(job_id: str):
        return service.tasks(job_id)

    @api.post("/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        return service.retry(job_id)

    @api.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        service.jobs.cancel(job_id)
        return service.jobs.get(job_id)

    @api.post("/jobs/{job_id}/publish")
    def publish(job_id: str, body: Activate):
        if service.jobs.get(job_id)["baseline"] != body.expectedRelease:
            raise SyncError("RELEASE_CONFLICT", "发布基线不一致", 409)
        with service.jobs.lease():
            service.publish(job_id)
        return service.jobs.get(job_id)

    @api.get("/releases")
    def releases():
        return service.status()["releases"]

    @api.post("/jobs/{job_id}/verify-battle", status_code=202)
    def verify_battle(job_id: str):
        import threading
        from packages.sync import verification

        service.jobs.get(job_id)

        def run():
            try:
                verification.verify(service, job_id)
            except SyncError as exc:
                service.jobs.update(
                    job_id,
                    battleReleaseStatus="failed",
                    error={"code": exc.code, "message": exc.message},
                )
            except Exception as exc:
                service.jobs.update(
                    job_id,
                    battleReleaseStatus="failed",
                    error={"code": "VERIFICATION_INTERNAL", "message": str(exc)[:300]},
                )

        threading.Thread(target=run, daemon=True).start()
        return {"jobId": job_id, "state": "queued"}

    @api.post("/jobs/{job_id}/publish-battle")
    def publish_battle(job_id: str, body: Activate):
        from packages.sync.verification import publish

        return publish(service, job_id, body.expectedRelease)

    @api.post("/rollback")
    def rollback(body: Rollback):
        return service.rollback(body.releaseId, body.expectedRelease)

    app.include_router(api)

    @app.middleware("http")
    async def release_context(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path.startswith("/api/sync/"):
            return await call_next(request)
        body = await request.body()
        release_id = service.pool.route(
            path, body, request.cookies.get("ptcg_practice_session")
        )
        if not release_id:
            if path.startswith("/api/battle/matches/") and request.method not in (
                "GET",
                "HEAD",
            ):
                # Authenticate ownership before exposing retirement status.
                from packages.collection.domain import DomainError

                try:
                    owner = request.app.state.battles.owner(
                        request.cookies.get("ptcg_practice_session")
                    )
                    mid = path.split("/")[4]
                    view = request.app.state.battles.snapshot(owner, mid)
                except DomainError as exc:
                    return JSONResponse(
                        {"code": exc.code, "message": exc.message},
                        status_code=exc.status,
                    )
                if view.get("readOnly"):
                    return JSONResponse(
                        {
                            "code": "RULE_RELEASE_UNAVAILABLE",
                            "message": view["readOnlyReason"],
                        },
                        status_code=409,
                    )
            return await call_next(request)
        # The outer local-only middleware has validated this host. Preserve it so
        # the frozen worker can validate same-origin writes on custom local ports.
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("content-length", "accept-encoding", "connection")
        }
        query = request.url.query
        try:
            result = await anyio.to_thread.run_sync(
                lambda: service.pool.get(release_id).request(
                    request.method, path + ("?" + query if query else ""), headers, body
                )
            )
        except SyncError as exc:
            return JSONResponse(
                {"code": exc.code, "message": exc.message}, status_code=exc.status
            )
        if (path == "/api/cards" and request.method == "GET" or
            path == "/api/battle/session" and request.method == "POST") and result["status"] == 200:
            import json

            payload = json.loads(result["body"])
            payload["cards"] = app.state.card_media.display(payload["cards"])
            result["body"] = json.dumps(payload, ensure_ascii=False).encode()
        result["headers"]["X-PTCG-Release"] = release_id
        return Response(
            result["body"], status_code=result["status"], headers=result["headers"]
        )

    app.add_event_handler("shutdown", service.close)

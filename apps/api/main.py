"""Loopback-only single-user API. Run via the root startup scripts."""

from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Annotated, Literal
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from packages.collection.domain import *
from packages.collection.store import Store

SOURCE_EXCEPTIONS = set(json.loads((ROOT / "data/cardpool/source-exceptions.json").read_text(encoding="utf8"))["printingIds"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entry(Input):
    printingId: str
    quantity: StrictInt


class Deck(Input):
    name: str = Field(min_length=1, max_length=80)
    entries: list[Entry] = Field(max_length=500)


class SaveDeck(Deck):
    expectedVersion: StrictInt


class Version(Input):
    expectedVersion: StrictInt


class Entries(Input):
    entries: list[Entry] = Field(max_length=500)


class Analysis(Entries):
    seed: StrictInt = 0


class CollectionEntry(Entry):
    condition: Literal["未标注", "全新", "良好", "使用痕迹"] = "未标注"
    notes: str = Field(default="", max_length=500)
    wishlist: bool = False


class CollectionUpdate(Version):
    changes: list[CollectionEntry] = Field(min_length=1, max_length=500)


class Import(Input):
    text: str = Field(max_length=200000)
    kind: Literal["deck", "collection"] = "deck"
    resolutions: dict[str, str] = Field(default_factory=dict)


class ApplyImport(Import):
    expectedVersion: StrictInt


class Missing(Entries):
    mode: Literal["exact", "equivalent"] = "exact"


def create_app(db_path=None):
    store = Store(db_path or os.getenv("PTCG_DB", str(ROOT / "var/app.sqlite")))
    app = FastAPI(title="PTCG Lab", version="1.0.1")
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=4)
    app.state.store = store
    from packages.collection.card_media import CardMedia, MAX_BYTES
    media = CardMedia(store)
    app.state.card_media = media
    from apps.api.battle import router
    battle_router, battle_service = router(store)
    app.include_router(battle_router)
    app.state.battles = battle_service

    from apps.api.sync import attach as attach_sync
    attach_sync(app, store)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Bind loopback, reject DNS rebinding and cross-site state mutations.
        host = request.url.hostname
        if host not in ("localhost", "127.0.0.1", "::1", "testserver"):
            return JSONResponse(
                {"code": "INVALID_HOST", "message": "仅提供本地访问"}, status_code=403
            )
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            allowed = {
                "http://127.0.0.1:8765",
                "http://localhost:8765",
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://testserver",
                str(request.base_url).rstrip("/"),
            }
            if (
                origin
                and origin not in allowed
                or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                return JSONResponse(
                    {"code": "INVALID_ORIGIN", "message": "拒绝跨站写入"},
                    status_code=403,
                )
            if (
                request.headers.get("content-length", "").isdigit()
                and int(request.headers["content-length"]) > (MAX_BYTES if request.url.path.endswith("/image") else 1048576)
            ):
                return JSONResponse(
                    {"code": "TOO_LARGE", "message": "请求过大"}, status_code=413
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, e):
        return JSONResponse(
            {"code": e.code, "message": e.message}, status_code=e.status
        )

    def rows(model):
        return [e.model_dump() for e in model.entries]

    image_dir = ROOT / ".catalog/images"
    if image_dir.exists():
        app.mount("/card-images", StaticFiles(directory=image_dir), name="card-images")
    app.mount("/uploaded-card-images", StaticFiles(directory=media.directory), name="uploaded-card-images")

    @app.get("/api/card-image-gaps")
    def image_gaps():
        import csv, io
        missing = [c for c in media.decorate(catalog_view()) if not c["image"].get("url")]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["卡牌ID", "名称", "扩充包编号", "卡号", "原因", "百科来源"])
        for c in missing:
            d = c["chineseDetails"]
            row = [c["printingId"], c["cnName"], c.get("productCode"), c.get("collectorNumber"), d.get("imageGap", "尚无卡图"), d.get("source")]
            writer.writerow(["'" + str(v) if str(v).startswith(("=", "+", "-", "@")) else v for v in row])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="missing-card-images.csv"'})

    def image_card(pid):
        return next(c for c in media.decorate(catalog_view()) if c["printingId"] == pid)

    @app.put("/api/cards/{pid}/image")
    async def upload_image(pid: str, request: Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise DomainError("TOO_LARGE", "卡图不能超过 8 MB", 413)
        media.upload(pid, bytes(data))
        return image_card(pid)

    @app.delete("/api/cards/{pid}/image")
    def remove_image(pid: str):
        media.remove(pid)
        return image_card(pid)

    @app.get("/api/meta")
    def meta():
        from packages.cardpool.scope import SCOPE, VERSION as SCOPE_VERSION
        return {
            "battleScope": {"id": SCOPE["id"], "allowedMarks": SCOPE["allowedMarks"], "basicEnergyTypes": SCOPE["basicEnergyTypes"], "version": SCOPE_VERSION},
            "catalogVersion": CATALOG_VERSION,
            "products": RAW.get("products", []),
            "cardCount": len(CARDS),
            "pendingSourceExceptionCount": len(SOURCE_EXCEPTIONS),
            "verifiedCount": sum(
                c["effectStatus"] == "verified" for c in CARDS.values()
            ),
            "formatId": RAW["formatId"],
            "asOf": AS_OF,
            "templates": RAW["templates"],
            "conditions": CONDITIONS,
            "mode": "local-single-user",
        }

    @app.get("/api/cards")
    def cards(
        q: str = "",
        category: str = "",
        product: str = "",
        owned: str = "",
        effect: str = "",
        element: str = "",
        pack: str = "",
    ):
        return {
            "cards": [{**c, "pendingSourceException": c["printingId"] in SOURCE_EXCEPTIONS} for c in media.decorate(catalog_view(
                q,
                category,
                product,
                owned,
                store.collection()["entries"],
                effect,
                element,
                pack,
            ))]
        }

    @app.get("/api/collection")
    def collection():
        return store.collection()

    @app.put("/api/collection")
    def update_collection(body: CollectionUpdate):
        return store.update_collection(
            [x.model_dump() for x in body.changes], body.expectedVersion
        )

    @app.get("/api/collection/export")
    def export_collection():
        return Response(
            export_csv(store.collection()["entries"], "collection"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="collection.csv"'},
        )

    @app.post("/api/import-preview")
    def preview(body: Import):
        return parse_import(body.text, body.kind, body.resolutions)

    @app.post("/api/collection/imports")
    def apply(body: ApplyImport):
        require(body.kind == "collection", "BAD_IMPORT_KIND", "此接口只导入收藏")
        return store.import_collection(
            body.text, body.resolutions, body.expectedVersion
        )

    @app.get("/api/collection/imports")
    def batches():
        return store.batches()

    @app.post("/api/collection/imports/{id}/undo")
    def undo(id: str, body: Version):
        return store.undo_import(id, body.expectedVersion)

    @app.get("/api/decks")
    def decks():
        return store.decks()

    @app.post("/api/decks", status_code=201)
    def create(body: Deck):
        return store.create_deck(body.name, rows(body))

    @app.get("/api/decks/{id}")
    def deck(id: str):
        return store.deck(id)

    @app.put("/api/decks/{id}")
    def save(id: str, body: SaveDeck):
        return store.save_deck(id, body.name, rows(body), body.expectedVersion)

    @app.delete("/api/decks/{id}")
    def delete_deck(id: str, body: Version):
        return store.delete_deck(id, body.expectedVersion)

    @app.get("/api/decks/{id}/export")
    def export_deck(id: str, format: Literal["csv", "text"] = "csv"):
        es = store.deck(id)["entries"]
        body = (
            export_csv(es, "deck")
            if format == "csv"
            else "\n".join(f"{e['quantity']} {e['printingId']}" for e in es)
        )
        return Response(
            body,
            media_type="text/csv; charset=utf-8"
            if format == "csv"
            else "text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="deck.{"csv" if format == "csv" else "txt"}"'
            },
        )

    @app.post("/api/decks/{id}/revisions")
    def freeze(id: str, body: Version):
        return store.freeze(id, body.expectedVersion)

    @app.delete("/api/decks/{id}/revisions/{revision_id}")
    def delete_revision(id: str, revision_id: str, body: Version):
        return store.delete_revision(id, revision_id, body.expectedVersion)

    @app.get("/api/decks/{id}/revisions")
    def revisions(id: str):
        return store.revisions(id)

    @app.post("/api/deck-validations")
    def check(body: Entries):
        return validation(rows(body))

    @app.post("/api/deck-analysis")
    def analyze(body: Analysis):
        return analysis(rows(body), body.seed)

    @app.post("/api/missing-cards")
    def missing_cards(body: Missing):
        return missing(rows(body), store.collection()["entries"], body.mode)

    dist = ROOT / "apps/web/dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(dist / "index.html")

    return app

"""Authenticated local practice endpoints. Never expose server replay or seed."""

from fastapi import APIRouter, Request, Response, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from typing import Literal
from packages.battle.service import COOKIE, runtime
from apps.api.expert_service import MatchService
from packages.collection.domain import RAW, catalog_view
from packages.collection.domain import require, normalize
from packages.battle.presets import PRESETS


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateMatch(Input):
    requestId: str = Field(min_length=8, max_length=100)
    revisionId: str = Field(min_length=1, max_length=100)
    opponent: str | None = Field(default=None, min_length=1, max_length=100)
    opponentRevisionId: str | None = Field(default=None, min_length=1, max_length=100)
    aiLevel: Literal["A1", "H2", "A2"] = "H2"

    @model_validator(mode="after")
    def one_opponent(self):
        if (self.opponent is None) == (self.opponentRevisionId is None):
            raise ValueError("请选择一个 AI 卡组版本或预组")
        return self


class Choice(Input):
    optionId: str | None = Field(default=None, max_length=100)
    selectedRefs: list[str] | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def exclusive(self):
        if (self.optionId is None) == (self.selectedRefs is None):
            raise ValueError("只提交一种选择")
        if self.selectedRefs and any(len(x) > 100 for x in self.selectedRefs):
            raise ValueError("无效引用")
        return self


class Command(Input):
    commandId: str = Field(pattern=r"^client-[A-Za-z0-9-]{8,90}$")
    expectedStateVersion: StrictInt = Field(ge=0)
    decisionId: str = Field(min_length=1, max_length=100)
    choice: Choice


class Resign(Input):
    expectedStateVersion: StrictInt = Field(ge=0)


def router(store):
    service = MatchService(store)
    api = APIRouter(prefix="/api/battle")

    def owner(request):
        return service.owner(request.cookies.get(COOKIE))

    @api.post("/session")
    def session(request: Request, response: Response):
        token, _ = service.session(request.cookies.get(COOKIE))
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="strict",
            max_age=31536000,
            secure=request.url.scheme == "https",
            path="/api/battle",
        )
        rt = runtime()
        return {
            "mode": "local-practice",
            "cards": request.app.state.card_media.decorate(catalog_view(effect="verified")),
            "engineVersion": rt.ENGINE_VERSION,
            "opponents": [{"id": t["id"], "name": t["name"]} for t in PRESETS + RAW["templates"]],
            "presets": [{"id": t["id"], "name": t["name"]} for t in PRESETS],
        }

    @api.post("/presets/{preset_id}/revision")
    def preset_revision(preset_id: str, request: Request):
        owner(request)
        preset = next((p for p in PRESETS if p["id"] == preset_id), None)
        require(preset is not None, "BAD_PRESET", "预设卡组不存在", 404)
        entries = normalize(preset["entries"])
        with service.lock:
            deck = next((d for d in store.decks() if d["name"] == preset["name"] and d["entries"] == entries), None)
            if deck is None:
                deck = store.create_deck(preset["name"], entries)
            return store.freeze(deck["id"], deck["version"])

    @api.get("/revisions")
    def revisions(request: Request):
        owner(request)
        return service.revisions()

    @api.get("/matches")
    def matches(request: Request):
        return service.list(owner(request))

    @api.post("/matches", status_code=201)
    def create(request: Request, body: CreateMatch):
        return service.create(owner(request), body.model_dump(exclude_none=True))

    @api.get("/matches/{id}")
    def view(request: Request, id: str):
        return service.snapshot(owner(request), id)

    @api.post("/matches/{id}/commands")
    def submit(request: Request, id: str, body: Command):
        return service.command(owner(request), id, body.model_dump(exclude_none=True))

    @api.post("/matches/{id}/advance")
    def advance(request: Request, id: str, steps: int = Query(default=8, ge=1, le=8)):
        return service.advance(owner(request), id, steps=steps)

    @api.post("/matches/{id}/resign")
    def resign(request: Request, id: str, body: Resign):
        return service.resign(owner(request), id, body.expectedStateVersion)

    @api.get("/matches/{id}/replay/timeline")
    def replay_timeline(request: Request, id: str):
        return service.replay_timeline(owner(request), id)

    @api.get("/matches/{id}/replay")
    def replay(
        request: Request,
        id: str,
        after: int = Query(default=-1, ge=-1),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        return service.replay(owner(request), id, after, limit)

    return api, service

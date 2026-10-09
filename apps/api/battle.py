"""Authenticated local practice endpoints. Never expose server replay or seed."""

from fastapi import APIRouter, Request, Response, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from typing import Literal
from packages.battle.service import MatchService, COOKIE, runtime
from packages.collection.domain import RAW, catalog_view


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateMatch(Input):
    requestId: str = Field(min_length=8, max_length=100)
    revisionId: str = Field(min_length=1, max_length=100)
    opponent: str | None = Field(default=None, min_length=1, max_length=100)
    opponentRevisionId: str | None = Field(default=None, min_length=1, max_length=100)
    aiLevel: Literal["A1", "A2"] = "A1"

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
            "opponents": [{"id": t["id"], "name": t["name"]} for t in RAW["templates"]],
        }

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

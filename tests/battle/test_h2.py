import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.battle import h2
from packages.battle.h2_v4 import policy
from packages.battle.decision import decide as a1
from packages.collection.domain import RAW


@pytest.fixture
def match(tmp_path, monkeypatch):
    monkeypatch.setattr("packages.battle.service.secrets.randbits", lambda n: 0)
    app = create_app(tmp_path / "h2.sqlite")
    c = TestClient(app)
    c.post("/api/battle/session", json={})
    store = app.state.store
    deck = store.create_deck("H2 test", RAW["templates"][0]["entries"])
    rev = store.freeze(deck["id"], deck["version"])
    req = dict(
        requestId="h2-test-0001",
        revisionId=rev["id"],
        opponent="dragapult",
        aiLevel="H2",
    )
    r = c.post("/api/battle/matches", json=req)
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["decision"] is None
    s = app.state.battles
    owner = s.owner(c.cookies.get("ptcg_practice_session"))
    return c, s, owner, v, req


def test_h2_version_recovery_idempotency_and_private_boundary(match):
    c, s, owner, v, req = match
    assert v["aiVersion"] == h2.VERSION
    assert c.post("/api/battle/matches", json=req).json()["matchId"] == v["matchId"]
    next_view = s.advance(owner, v["matchId"])
    assert next_view["stateVersion"] == 1
    with s.db() as db:
        row = s._row(db, owner, v["matchId"])
        record = json.loads(row["body"])
        assert record["config"]["provenance"]["aiSourceHash"] == h2.fingerprint()
    s.cache.clear()
    assert s.snapshot(owner, v["matchId"]) == next_view
    public = c.get(f"/api/battle/matches/{v['matchId']}/replay").text
    assert (
        "aiSourceHash" not in public
        and "deck2" not in public
        and '"seed"' not in public
    )
    default = c.post(
        "/api/battle/matches",
        json={
            k: value
            for k, value in dict(req, requestId="default-0001").items()
            if k != "aiLevel"
        },
    ).json()
    assert default["aiVersion"] == h2.VERSION
    from packages.battle.agent import VERSION
    legacy = c.post("/api/battle/matches", json={**req, "requestId": "explicit-a1-0001", "aiLevel": "A1"}).json()
    assert legacy["aiVersion"] == VERSION


def test_h2_source_change_blocks_resume(match, monkeypatch):
    _, s, owner, v, _ = match
    monkeypatch.setattr(h2, "fingerprint", lambda: "different")
    with pytest.raises(Exception, match="H2 策略"):
        s.advance(owner, v["matchId"])


def test_h2_concurrent_advance_commits_once(match, monkeypatch):
    _, s, owner, v, _ = match
    barrier = threading.Barrier(2)
    actual = h2.decide

    def concurrent(dto):
        assert dto["observation"]["opponent"]["hand"] is None
        barrier.wait(timeout=5)
        return actual(dto)

    monkeypatch.setattr(h2, "decide", concurrent)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: s.advance(owner, v["matchId"]), range(2)))
    assert all(r["stateVersion"] == 1 for r in results)


def test_h2_resign_during_compute_discards_proposal(match, monkeypatch):
    _, s, owner, v, _ = match
    entered, release = threading.Event(), threading.Event()
    actual = h2.decide

    def suspended(dto):
        entered.set()
        assert release.wait(5)
        return actual(dto)

    monkeypatch.setattr(h2, "decide", suspended)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(s.advance, owner, v["matchId"])
        assert entered.wait(3)
        try:
            assert (
                pool.submit(s.resign, owner, v["matchId"], 0).result(timeout=2)[
                    "status"
                ]
                == "resigned"
            )
        finally:
            release.set()
        assert pending.result(timeout=3)["status"] == "resigned"


@pytest.mark.parametrize("failure", ["invalid", "exception", "slow"])
def test_h2_fallback_is_valid_and_diagnostic(match, monkeypatch, failure):
    _, s, owner, v, _ = match

    def broken(dto):
        if failure == "exception":
            raise RuntimeError("private details must not escape")
        if failure == "invalid":
            return {"choice": {"optionId": "missing"}}, False
        return a1(dto)

    monkeypatch.setattr(policy, "decide", broken)
    if failure == "slow":
        clock = iter(range(100000))
        monkeypatch.setattr(
            "packages.battle.h2.time.perf_counter", lambda: float(next(clock)) * 2
        )
    from packages.battle.service import runtime

    with s.db() as db:
        game = s._game(s._row(db, owner, v["matchId"]))
        dto = runtime().view(game, game.actor)
    before = copy.deepcopy(dto)
    command, detail = h2.decide(dto)
    from packages.simulation.a2 import legal

    assert legal(dto, command) and dto == before
    assert detail["fallback"]
    assert "private details" not in json.dumps(detail)


@pytest.mark.parametrize("family_index", range(5))
def test_complete_app_game_each_family(tmp_path, monkeypatch, family_index):
    from pathlib import Path
    from apps.api.expert_service import MatchService
    from packages.battle.agent import predict

    monkeypatch.setattr(
        "packages.battle.service.secrets.randbits", lambda n: 21630000 + family_index
    )
    from packages.battle.presets import PRESETS
    deck = PRESETS[family_index]
    app = create_app(tmp_path / "complete.sqlite")
    client = TestClient(app)
    client.post("/api/battle/session", json={})
    store = app.state.store
    saved = store.create_deck("H2 " + deck["family"], deck["entries"])
    revision = store.freeze(saved["id"], saved["version"])
    request = dict(
        requestId="complete-h2-0001",
        revisionId=revision["id"],
        opponentRevisionId=revision["id"],
        aiLevel="H2",
    )
    response = client.post("/api/battle/matches", json=request)
    assert response.status_code == 201, response.text
    v = response.json()
    for step in range(2500):
        if v["done"]:
            break
        if step == 20:
            # Exercise a fresh service, not just the old in-memory engine cache.
            app.state.battles = MatchService(store)
        if v["decision"]:
            command, _ = predict(v)
            command["commandId"] = f"client-complete-{step:08d}"
            response = client.post(
                f"/api/battle/matches/{v['matchId']}/commands", json=command
            )
            assert response.status_code == 200, response.text
            v = response.json()["view"]
        else:
            response = client.post(f"/api/battle/matches/{v['matchId']}/advance")
            assert response.status_code == 200, response.text
            v = response.json()
    assert v["done"] and v["winner"] in ("PLAYER1", "PLAYER2"), v
    service = app.state.battles
    with service.db() as db:
        frames = [
            json.loads(r["body"]) for r in db.execute("SELECT body FROM match_frames")
        ]
        # Details may be nested in event lists; count by serialized flag, without exposing private logs.
        assert not any('"fallback": true' in json.dumps(f) for f in frames)
    service.cache.clear()
    owner = service.owner(client.cookies.get("ptcg_practice_session"))
    assert service.snapshot(owner, v["matchId"])["winner"] == v["winner"]

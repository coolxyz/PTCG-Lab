import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.collection.domain import RAW
from packages.battle.agent import predict
from packages.simulation.a2 import VERSION


@pytest.fixture
def match(tmp_path, monkeypatch):
    monkeypatch.setattr("packages.battle.service.secrets.randbits", lambda n: 0)
    app = create_app(tmp_path / "a2.sqlite")
    client = TestClient(app)
    client.post("/api/battle/session", json={})
    store = app.state.store
    deck = store.create_deck("A2 测试", RAW["templates"][0]["entries"])
    revision = store.freeze(deck["id"], deck["version"])
    response = client.post(
        "/api/battle/matches",
        json={
            "requestId": "a2-fixture-001",
            "revisionId": revision["id"],
            "opponent": "dragapult",
            "aiLevel": "A2",
        },
    )
    assert response.status_code == 201
    initial = response.json()
    assert initial["decision"] is None
    service = app.state.battles
    owner = service.owner(client.cookies.get("ptcg_practice_session"))
    return client, service, owner, initial


def test_real_worker_persisted_information_and_public_replay(match):
    client, service, owner, initial = match
    result = service.advance(owner, initial["matchId"])
    assert result["stateVersion"] == 1 and result["aiVersion"] == VERSION
    with service.db() as db:
        rows = list(db.execute("SELECT seq,body FROM match_ai_views ORDER BY seq"))
    assert [r["seq"] for r in rows] == [0, 1]
    for row in rows:
        private_view = json.loads(row["body"])
        assert private_view["observation"]["opponent"]["hand"] is None
        assert "seed" not in private_view and "config" not in private_view
    replay = client.get(f"/api/battle/matches/{initial['matchId']}/replay").json()
    assert "match_ai_views" not in json.dumps(replay)
    service.cache.clear()
    assert service.snapshot(owner, initial["matchId"])["stateVersion"] == 1


def test_search_releases_authority_lock_and_discards_proposal_after_resign(
    match, monkeypatch
):
    _, service, owner, initial = match
    entered, release = threading.Event(), threading.Event()

    def suspended(info):
        info.validate()
        entered.set()
        assert release.wait(5)
        return {
            "command": predict(info.observations[-1])[0],
            "status": "searched",
            "simulations": 2,
            "particles": 1,
            "seconds": 0.1,
        }

    monkeypatch.setattr("packages.simulation.a2.decide", suspended)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(service.advance, owner, initial["matchId"])
        assert entered.wait(3)
        try:
            resigned = pool.submit(service.resign, owner, initial["matchId"], 0).result(
                timeout=2
            )
            assert resigned["status"] == "resigned"
        finally:
            release.set()
        assert future.result(timeout=3)["status"] == "resigned"
    with service.db() as db:
        count = db.execute("SELECT COUNT(*) FROM match_ai_views").fetchone()[0]
    assert count == 1  # Resign adds no synthetic AI action/history.


def test_two_simultaneous_searches_commit_only_one_choice(match, monkeypatch):
    _, service, owner, initial = match
    barrier = threading.Barrier(2)

    def simultaneous(info):
        barrier.wait(timeout=3)
        return {
            "command": predict(info.observations[-1])[0],
            "status": "searched",
            "simulations": 2,
            "particles": 1,
            "seconds": 0.1,
        }

    monkeypatch.setattr("packages.simulation.a2.decide", simultaneous)
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(service.advance, owner, initial["matchId"]) for _ in range(2)
        ]
        results = [f.result(timeout=5) for f in futures]
    assert all(r["stateVersion"] == 1 for r in results)
    with service.db() as db:
        assert db.execute("SELECT COUNT(*) FROM match_ai_views").fetchone()[0] == 2

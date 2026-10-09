import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.collection.domain import RAW, DomainError
from packages.battle.service import MatchService, runtime
from packages.battle.agent import predict


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("packages.battle.service.secrets.randbits", lambda n: 7)
    app = create_app(tmp_path / "app.sqlite")
    client = TestClient(app)
    assert client.post("/api/battle/session", json={}).status_code == 200
    store = app.state.store
    t = RAW["templates"][0]
    deck = store.create_deck("P2 测试", t["entries"])
    revision = store.freeze(deck["id"], deck["version"])
    service = app.state.battles
    owner = service.owner(client.cookies.get("ptcg_practice_session"))
    req = {
        "requestId": "test-create-001",
        "revisionId": revision["id"],
        "opponent": "dragapult",
    }
    return app, client, service, owner, req


def create(setup):
    app, c, s, o, req = setup
    r = c.post("/api/battle/matches", json=req)
    assert r.status_code == 201, r.text
    v = r.json()
    for _ in range(20):
        if v["decision"] or v["done"]:
            return v
        v = c.post("/api/battle/matches/" + v["matchId"] + "/advance").json()
    raise AssertionError("AI stalled")


def command(v, id="client-test00001"):
    c, _ = predict(v)
    c["commandId"] = id
    return c


def test_deleting_deck_keeps_existing_match_readable(setup):
    app, c, service, owner, req = setup
    view = create(setup)
    deck = app.state.store.decks()[0]
    response = c.request("DELETE", "/api/decks/" + deck["id"],
                         json={"expectedVersion": deck["version"]})
    assert response.status_code == 200
    assert service.revisions() == []
    response = c.get("/api/battle/matches/" + view["matchId"])
    assert response.status_code == 200
    assert response.json()["matchId"] == view["matchId"]


def test_admission_and_creation_idempotency(setup):
    app, c, s, owner, req = setup
    v = create(setup)
    r = c.post("/api/battle/matches", json=req)
    assert r.json()["matchId"] == v["matchId"]
    assert (
        c.post("/api/battle/matches", json={**req, "opponent": "gholdengo"}).status_code
        == 409
    )
    # Changing a saved snapshot or claiming unsupported effects is rejected.
    with s.store.db(True) as db:
        body = json.loads(
            db.execute(
                "SELECT body FROM revisions WHERE id=?", (req["revisionId"],)
            ).fetchone()[0]
        )
        body["catalogVersion"] = "old"
        db.execute(
            "UPDATE revisions SET body=? WHERE id=?",
            (json.dumps(body), req["revisionId"]),
        )
    r = c.post("/api/battle/matches", json={**req, "requestId": "new-request"})
    assert r.status_code == 409 and r.json()["code"] == "REVISION_CORRUPT"
    assert c.post("/api/battle/matches", json={**req, "seed": 7}).status_code == 422


def test_access_control_and_no_private_replay(setup):
    app, c, s, o, req = setup
    v = create(setup)
    id = v["matchId"]
    other = TestClient(app)
    for suffix in ("", "/replay", "/replay/timeline"):
        assert other.get("/api/battle/matches/" + id + suffix).status_code == 401
    other.post("/api/battle/session", json={})
    for suffix in ("", "/replay", "/replay/timeline"):
        assert other.get("/api/battle/matches/" + id + suffix).status_code == 404
    assert other.post("/api/battle/matches/" + id + "/advance").status_code == 404
    assert (
        other.post(
            "/api/battle/matches/" + id + "/resign",
            json={"expectedStateVersion": v["stateVersion"]},
        ).status_code
        == 404
    )
    assert other.get("/api/battle/matches").json() == []
    assert (
        c.post(
            "/api/battle/session", json={}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    data = c.get("/api/battle/matches/" + id + "/replay").json()
    encoded = json.dumps(data)
    assert not any(
        '"' + key + '"' in encoded
        for key in ("seed", "config", "deck1", "deck2", "stateDigest", "commands")
    )
    for frame in data["frames"]:
        assert frame["view"]["observation"]["opponent"]["hand"] is None
    cookie = c.post("/api/battle/session", json={}).headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie


def test_ai_single_step_advance(setup):
    app, c, s, owner, req = setup
    v = create(setup)
    url = "/api/battle/matches/" + v["matchId"]
    for i in range(100):
        if not v["decision"]:
            break
        v = c.post(url + "/commands", json=command(v, f"client-single-step-{i:04d}")).json()["view"]
    assert not v["done"] and not v["decision"]
    assert c.post(url + "/advance?steps=0", json={}).status_code == 422
    after = c.post(url + "/advance?steps=1", json={}).json()
    assert after["stateVersion"] == v["stateVersion"] + 1


def test_commands_retry_conflict_stale_forged_actor_and_restart(setup):
    app, c, s, owner, req = setup
    v = create(setup)
    id = v["matchId"]
    cmd = command(v)
    url = "/api/battle/matches/" + id + "/commands"
    first = c.post(url, json=cmd)
    assert first.status_code == 200, first.text
    assert c.post(url, json=cmd).json()["receipt"] == first.json()["receipt"]
    bad = {**cmd, "choice": {"optionId": "nope"}}
    assert c.post(url, json=bad).status_code == 409
    assert c.post(url, json={**cmd, "commandId": "client-other0001"}).status_code == 409
    assert c.post(url, json={**cmd, "playerId": "PLAYER2"}).status_code == 422
    assert c.post(url, json={**cmd, "commandId": "ai-5"}).status_code == 422
    resumed = MatchService(s.store)
    assert resumed.snapshot(owner, id) == s.snapshot(owner, id)
    assert resumed.command(owner, id, cmd)["receipt"] == first.json()["receipt"]
    # Actual engine reconstruction, not only reading a stored projection.
    with resumed.db() as db:
        game = resumed._game(resumed._row(db, owner, id))
    assert (
        runtime().view(game, runtime().PlayerId.PLAYER1)["observation"]
        == first.json()["view"]["observation"]
    )


def test_two_writers_single_acceptance(setup):
    app, c, s, owner, req = setup
    v = create(setup)
    id = v["matchId"]
    second = MatchService(s.store)
    cmds = [command(v, "client-concurrent1"), command(v, "client-concurrent2")]

    def send(pair):
        service, cmd = pair
        try:
            return service.command(owner, id, cmd)["receipt"]
        except DomainError as e:
            return e.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(send, [(s, cmds[0]), (second, cmds[1])]))
    assert sum(isinstance(x, dict) for x in results) == 1
    assert s.snapshot(owner, id)["stateVersion"] == v["stateVersion"] + 1


def test_failed_persist_discards_mutated_engine(setup, monkeypatch):
    app, c, s, owner, req = setup
    v = create(setup)
    id = v["matchId"]
    cmd = command(v)
    original = s._save

    def fail(*args):
        original(*args)
        raise OSError("disk full after writes before commit")

    monkeypatch.setattr(s, "_save", fail)
    with pytest.raises(OSError):
        s.command(owner, id, cmd)
    assert s.snapshot(owner, id) == v
    assert id not in s.cache
    monkeypatch.setattr(s, "_save", original)
    result = s.command(owner, id, cmd)
    assert result["receipt"]["stateVersion"] == v["stateVersion"] + 1


def test_hidden_selection_and_view_noninterference(setup):
    rt = runtime()
    from ptcg.core.action import ChooseCardActionSpace

    g = rt.Adapter(7)
    candidates = list(g.env.gamestate.player1.left[:6])
    g.info["raw_available_actions"] = ChooseCardActionSpace(
        rt.PlayerId.PLAYER2, rt.PlayerId.PLAYER1, 1, 1, candidates, hidden=True
    )
    a = rt.view(g, rt.PlayerId.PLAYER2)
    g.actions.candidates.reverse()
    b = rt.view(g, rt.PlayerId.PLAYER2)
    assert a == b
    assert all(set(c) == {"ref"} for c in a["decision"]["candidates"])
    before = g.private_digest()
    a["observation"]["self"]["hand"].clear()
    assert g.private_digest() == before


def test_terminal_resign_replay_is_read_only(setup):
    app, c, s, owner, req = setup
    v = create(setup)
    id = v["matchId"]
    v = s.resign(owner, id, v["stateVersion"])
    assert (
        v["status"] == "resigned" and v["winner"] == "PLAYER2" and v["decision"] is None
    )
    assert v["result"]["reason"] == "resigned"
    assert s.advance(owner, id) == v
    assert s.resign(owner, id, v["stateVersion"]) == v
    assert s.replay(owner, id)["frames"][-1]["view"]["status"] == "resigned"
    assert c.get("/api/battle/matches/" + id + "/replay?limit=1000").status_code == 422


def test_full_match_ai_prompt_restore_and_private_frames(setup):
    app, c, s, owner, req = setup
    v = create(setup)
    id = v["matchId"]
    restored = set()
    for step in range(1500):
        if v["done"]:
            break
        if v["decision"]:
            d = v["decision"]
            key = (v["phase"], d["kind"], d.get("hidden"), d.get("source"))
            if key not in restored:
                restored.add(key)
                s = MatchService(s.store)
            v = s.command(owner, id, command(v, "client-fullgame" + str(step)))["view"]
        else:
            v = s.advance(owner, id)
    assert v["done"] and v["status"] == "finished"
    assert v["result"]["reason"] in {"prizes_taken", "no_pokemon", "deck_out"}
    assert any(k[1] == "selection" for k in restored)
    after = -1
    while after < v["stateVersion"]:
        data = s.replay(owner, id, after)
        for f in data["frames"]:
            assert f["view"]["observation"]["opponent"]["hand"] is None
        after = data["nextAfter"]


def test_ai_invalid_proposal_falls_back_without_hidden_state(setup, monkeypatch):
    from packages.battle.decision import decide
    from packages.battle import agent

    v = create(setup)
    monkeypatch.setattr(
        agent, "predict", lambda v: ({"choice": {"optionId": "invalid"}}, "invalid")
    )
    cmd, fallback = decide(v)
    assert fallback
    app, c, s, owner, req = setup
    cmd["commandId"] = "client-fallback0001"
    result = s.command(owner, v["matchId"], cmd)
    assert result["receipt"]["accepted"]


def test_engine_version_mismatch_blocks_mutation_but_keeps_replay(setup):
    app, c, s, owner, req = setup
    v = create(setup)
    id = v["matchId"]
    with s.db(True) as db:
        db.execute("UPDATE matches SET engine_version='unknown' WHERE id=?", (id,))
    with pytest.raises(DomainError, match="内核版本已变化"):
        s.command(owner, id, command(v))
    assert s.replay(owner, id)["frames"]


def test_old_revision_revalidated_without_mutating_snapshot(setup):
    from packages.collection.domain import content_hash

    app, c, s, owner, req = setup
    with s.db(True) as db:
        body = json.loads(
            db.execute(
                "SELECT body FROM revisions WHERE id=?", (req["revisionId"],)
            ).fetchone()[0]
        )
        body["catalogVersion"] = "old-catalog"
        body["effectReleaseVersion"] = "old-effects"
        body["formatHash"] = "old-format"
        body["effectMappings"] = {}
        db.execute(
            "UPDATE revisions SET body=?,hash=? WHERE id=?",
            (json.dumps(body), content_hash(body), req["revisionId"]),
        )
    response = c.post("/api/battle/matches", json=req)
    assert response.status_code == 201, response.text
    assert s.revisions()[0]["playable"]
    with s.db() as db:
        saved = json.loads(db.execute("SELECT body FROM revisions WHERE id=?", (req["revisionId"],)).fetchone()[0])
        assert saved == body
        match = json.loads(db.execute("SELECT body FROM matches WHERE id=?", (response.json()["matchId"],)).fetchone()[0])
        assert match["config"]["provenance"]["catalogVersion"] != "old-catalog"
    assert s.store.freeze(app.state.store.decks()[0]["id"], 1)["id"] == req["revisionId"]
    request = {"requestId": "custom-old-opponent", "revisionId": req["revisionId"], "opponentRevisionId": req["revisionId"]}
    assert c.post("/api/battle/matches", json=request).status_code == 201


def test_public_targets_include_player_side_for_mirror_matches():
    rt = runtime()
    from ptcg.core.action import ChooseCardActionSpace

    game = rt.Adapter(42)
    for _ in range(30):
        if game.env.phase == "playing":
            break
        cmd, _ = predict(rt.view(game, game.actor))
        game.submit(game.actor, cmd)
    first, second = game.env.get_players()
    game.info["raw_available_actions"] = ChooseCardActionSpace(
        first.id, first.id, 1, 1, first.active + second.active, indexed=True
    )
    candidates = rt.view(game, first.id)["decision"]["candidates"]
    assert {c["side"] for c in candidates} == {"self", "opponent"}
    assert all(c["position"] == "CardPosition.ACTIVE" for c in candidates)


def test_effect_release_mismatch_blocks_mutation_but_preserves_replay(setup):
    app, c, service, owner, req = setup
    current = create(setup)
    with service.db(True) as db:
        row = db.execute(
            "SELECT body FROM matches WHERE id=?", (current["matchId"],)
        ).fetchone()
        body = json.loads(row[0])
        body["config"]["provenance"]["effectReleaseVersion"] = "older-release"
        db.execute(
            "UPDATE matches SET body=? WHERE id=?",
            (json.dumps(body), current["matchId"]),
        )
    with pytest.raises(DomainError, match="效果发布版本已变化"):
        service.command(owner, current["matchId"], command(current))
    assert service.replay(owner, current["matchId"])["frames"]


def test_replay_timeline_is_public_read_only_and_works_without_engine(setup, monkeypatch):
    app, client, service, owner, req = setup
    view = create(setup)
    for step in range(120):
        if view['done'] or view['observation']['turn_number'] >= 3:
            break
        if view['decision']:
            view = service.command(owner, view['matchId'], command(view, f'client-timeline-{step:08d}'))['view']
        else:
            view = service.advance(owner, view['matchId'])
    assert view['observation']['turn_number'] >= 3
    match_id = view['matchId']
    with service.db() as db:
        before = list(db.iterdump())
        frames = [json.loads(r[0]) for r in db.execute(
            'SELECT body FROM match_frames WHERE match_id=? ORDER BY seq', (match_id,))]
    def unavailable():
        raise AssertionError('Replay indexing must not load the engine')
    monkeypatch.setattr('packages.battle.service.runtime', unavailable)
    result = client.get(f'/api/battle/matches/{match_id}/replay/timeline')
    assert result.status_code == 200
    data = result.json()
    assert data['lastSeq'] == view['stateVersion']
    assert data['checkpoints'][0] == {'seq': 0, 'turn': 0, 'player': None, 'phase': 'setup'}
    expected = []
    last = None
    for frame in frames:
        obs = frame['observation']
        if frame['phase'] != 'playing':
            continue
        key = (obs['turn_number'], obs['turn'])
        if key != last:
            expected.append({'seq': frame['stateVersion'], 'turn': key[0], 'player': key[1], 'phase': 'playing'})
            last = key
    assert data['checkpoints'][1:] == expected
    assert len(expected) >= 3
    assert all(set(p) == {'seq', 'turn', 'player', 'phase'} for p in data['checkpoints'])
    with service.db() as db:
        assert list(db.iterdump()) == before


def test_delete_revision_preserves_existing_match_and_can_restore(setup):
    app, c, s, owner, req = setup
    v = create(setup)
    deck = s.store.decks()[0]
    url = f"/api/decks/{deck['id']}/revisions/{req['revisionId']}"
    with s.db() as db:
        before = db.execute('SELECT body FROM matches WHERE id=?', (v['matchId'],)).fetchone()[0]
    assert c.request('DELETE', url, json={'expectedVersion': 999}).status_code == 409
    assert c.request('DELETE', url, json={'expectedVersion': deck['version']}).status_code == 200
    assert s.revisions() == []
    assert c.post('/api/battle/matches', json={**req, 'requestId': 'deleted-version-test'}).json()['code'] == 'REVISION_DELETED'
    assert c.get('/api/battle/matches/'+v['matchId']).status_code == 200
    assert c.get('/api/battle/matches/'+v['matchId']+'/replay').status_code == 200
    with s.db() as db:
        assert db.execute('SELECT body FROM matches WHERE id=?', (v['matchId'],)).fetchone()[0] == before
    assert s.store.freeze(deck['id'], deck['version'])['id'] == req['revisionId']
    assert s.revisions()[0]['playable']
    other = s.store.create_deck('其他卡组', deck['entries'])
    assert c.request('DELETE', f"/api/decks/{other['id']}/revisions/{req['revisionId']}", json={'expectedVersion': 1}).status_code == 404

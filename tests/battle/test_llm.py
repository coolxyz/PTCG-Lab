import json
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.battle import allocations, llm
from packages.battle.agent import predict
from packages.collection.domain import RAW


def test_card_definitions_preserve_selection_and_deck_inspection():
    card = {
        "id": "same-printing",
        "name": "Example",
        "hp": 90,
        "cardType": "DRAGON",
        "attacks": [{"name": "Attack", "cost": ["FIRE", "PSYCHIC"]}],
    }
    active = {
        **card,
        "ref": "v1:self:active:0",
        "hp": 30,
        "attacks": [{"name": "Attack", "cost": ["FIRE"]}],
    }
    inspection = {
        "owner": "self",
        "ordered": False,
        "total": 2,
        "cards": [{"card": card, "count": 2}],
    }
    visible = {
        "observation": {"self": {"active": [active]}, "opponent": {}},
        "decision": {
            "id": "d1",
            "kind": "selection",
            "min": 0,
            "max": 1,
            "candidates": [{"ref": "d1:c0", "card": card}, {"ref": "d1:c1"}],
            "deckInspection": inspection,
        },
    }
    compact = llm.compact_payload(visible)
    common = compact["cardDefinitions"]["same-printing"]
    assert common == {"cardType": "DRAGON"}
    assert {**common, **compact["observation"]["self"]["active"][0]} == active
    d = compact["decision"]
    assert d["min"] == 0 and d["max"] == 1
    assert {**common, **d["candidates"][0]["card"]} == card
    assert d["candidates"][1] == {"ref": "d1:c1"}
    deck = d["deckInspection"]
    assert deck["owner"] == "self" and deck["ordered"] is False and deck["total"] == 2
    assert deck["cards"][0]["count"] == 2
    assert {**common, **deck["cards"][0]["card"]} == card
    assert (
        visible["decision"]["deckInspection"]["cards"][0]["card"]["cardType"]
        == "DRAGON"
    )


@pytest.mark.parametrize(
    ("effect", "source", "side", "origin", "count"),
    [
        (
            allocations.PHANTOM_DIVE,
            "TWM-200",
            "opponent",
            {"actionType": "AttackAction", "attack": {"name": "Phantom Dive"}},
            6,
        ),
        (
            allocations.PUNK_UP,
            "P4P-5485406C2C86",
            "self",
            {
                "sourceId": "P4P-5485406C2C86",
                "zones": ["CardPosition.LEFT"],
                "selected": ["Darkness Energy"] * 3,
            },
            3,
        ),
    ],
)
def test_allocation_plan_one_request_and_fresh_refs(
    monkeypatch, effect, source, side, origin, count
):
    pokemon = [object(), object()]
    game = SimpleNamespace(
        actor="PLAYER2",
        version=8,
        done=False,
        info={"prompt": SimpleNamespace(source=object())},
        actions=SimpleNamespace(candidates=pokemon[:]),
        _candidate_order=lambda: [0, 1],
    )

    def project(g, _):
        return {
            "stateVersion": g.version,
            "phase": "playing",
            "observation": {"self": {"hand": []}, "opponent": {"hand": None}},
            "decision": {
                "id": f"d{g.version}",
                "kind": "selection",
                "hidden": False,
                "sourceId": source,
                "zones": ["CardPosition.BENCH"],
                "min": 1,
                "max": 1,
                "candidates": [
                    {
                        "ref": f"d{g.version}:c{i}",
                        "boardRef": f"v{g.version}:{side}:bench:{pokemon.index(card)}",
                        "card": {"name": "同名宝可梦"},
                        "side": side,
                    }
                    for i, card in enumerate(g.actions.candidates)
                ],
            },
        }

    before = project(game, game.actor)
    config = llm.Settings(
        baseUrl="http://localhost:1234/v1", model="test", responseFormat="json_schema"
    )
    calls = []

    def provider(cfg, visible):
        spec = visible["allocation"]
        calls.append(spec)
        schema = llm._response_format(cfg, visible["decision"], spec)[
            "response_format"
        ]["json_schema"]["schema"]
        refs_schema = schema["properties"]["choice"]["properties"]["allocationRefs"]
        assert refs_schema["minItems"] == refs_schema["maxItems"] == count
        assert refs_schema["items"]["enum"] == ["d8:c0", "d8:c1"]
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "choice": {"allocationRefs": ["d8:c0"] * count},
                                "reason": "集中分配",
                            }
                        )
                    },
                }
            ]
        }

    monkeypatch.setattr(llm, "_request", provider)
    command, detail = llm.decide(before, [{"ownAction": origin}], config, [])
    assert detail["status"] == "model" and command["choice"] == {
        "selectedRefs": ["d8:c0"]
    }
    assert len(calls) == 1 and calls[0]["effect"] == effect
    assigned = []
    for _, step in allocations.follow(game, before, detail["allocationPlan"], project):
        ref = step["choice"]["selectedRefs"][0]
        assigned.append(game.actions.candidates[int(ref.rsplit("c", 1)[-1])])
        assert step["expectedStateVersion"] == game.version
        game.actions.candidates.reverse()
        game.version += 1
    assert assigned == [pokemon[0]] * count and len(calls) == 1

    game.version = 8
    game.actions.candidates = pokemon[:]
    replay = allocations.follow(game, before, detail["allocationPlan"], project)
    assert next(replay)[1] == command
    game.version += 1
    game.actor = "PLAYER1"
    assert list(replay) == []

    invalid = ["d8:c0"] * (count - 1) + ["stale"]
    assert not allocations.valid_plan(invalid, calls[0])


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("packages.battle.service.secrets.randbits", lambda _: 7)
    for name in (
        "BASE_URL",
        "MODEL",
        "API_KEY",
        "TIMEOUT_SECONDS",
        "MAX_RETRIES",
        "MAX_TOKENS",
        "REASONING_EFFORT",
        "RESPONSE_FORMAT",
    ):
        monkeypatch.delenv(f"PTCG_LLM_{name}", raising=False)
    app = create_app(tmp_path / "app.sqlite")
    client = TestClient(app)
    assert client.post("/api/battle/session").json()["llmOpponent"] is None
    store = app.state.store
    deck = store.create_deck("LLM 练习测试", RAW["templates"][0]["entries"])
    revision = store.freeze(deck["id"], deck["version"])
    create = {
        "requestId": "llm-test-create",
        "revisionId": revision["id"],
        "opponent": "dragapult",
        "aiLevel": "LLM",
    }
    assert (
        client.post("/api/battle/matches", json=create).json()["code"]
        == "LLM_NOT_CONFIGURED"
    )
    config = tmp_path / "llm-opponent.json"
    config.write_text(
        json.dumps(
            {
                "baseUrl": "https://provider.example/v1",
                "model": "test-battle-model",
                "apiKey": "example-secret-for-test",
                "reasoningEffort": "low",
            }
        )
    )
    session = client.post("/api/battle/session")
    assert session.json()["llmOpponent"]["name"] == "LLM · test-battle-model"
    assert (
        "example-secret-for-test" not in session.text
        and "provider.example" not in session.text
    )
    opened = client.post("/api/battle/matches", json=create)
    assert opened.status_code == 201, opened.text
    return app, client, opened.json(), config


def human_step(client, url, view):
    command, _ = predict(view)
    command["commandId"] = f"client-llm-{view['stateVersion']:08d}"
    response = client.post(url + "/commands", json=command)
    assert response.status_code == 200, response.text
    return response.json()["view"]


def test_llm_live_choices_resume_and_review(tmp_path, monkeypatch):
    app, client, view, config = setup(tmp_path, monkeypatch)
    url = "/api/battle/matches/" + view["matchId"]
    assert view["aiStrategy"]["id"] == "llm"
    assert client.get(url + "/llm-review").status_code == 409
    seen = []
    waits = []
    sessions = []
    original_client = httpx.Client
    monkeypatch.setattr(llm, "sleep", waits.append)

    def provider(request):
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer example-secret-for-test"
        assert "cookie" not in request.headers
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["reasoning_effort"] == "low"
        data = json.loads(body["messages"][1]["content"])
        assert "hand" not in data["observation"]["opponent"]
        assert "seed" not in data and "provider.example" not in request.content.decode()
        assert sum(entry["count"] for entry in data["ownDeck"]) == 60
        assert data["ownDeck"] == sorted(
            data["ownDeck"], key=lambda entry: entry["card"]
        )
        seen.append(data)
        if len(seen) == 1:
            raise httpx.RemoteProtocolError("provider-private", request=request)
        if len(seen) >= 3:
            return httpx.Response(429, json={"error": "provider-private"})
        decision = data["decision"]
        choice = (
            {"optionId": decision["options"][0]["id"]}
            if decision["kind"] == "options"
            else {
                "selectedRefs": [
                    item["ref"] for item in decision["candidates"][: decision["min"]]
                ]
            }
        )
        return httpx.Response(
            200,
            headers={"Set-Cookie": "provider-cookie=private; Path=/"},
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {"choice": choice, "reason": "private-test-rationale"}
                            ),
                            "reasoning_content": "provider-private-reasoning",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 9,
                    "total_tokens": 30,
                },
            },
        )

    def http_client(**kwargs):
        session = original_client(transport=httpx.MockTransport(provider), **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(llm.httpx, "Client", http_client)
    for _ in range(50):
        if view["decision"]:
            view = human_step(client, url, view)
        else:
            app.state.battles.cache.clear()  # Restore without re-requesting old model decisions.
            advanced = client.post(url + "/advance")
            assert advanced.status_code == 200, advanced.text
            view = advanced.json()
        if len(seen) == 5:
            break
        assert not view["done"]
    assert len(seen) == 5
    assert len(sessions) == 1
    assert seen[0] == seen[1] and seen[2] == seen[3] == seen[4]
    assert waits == [0.75, 0.75, 1.5]
    assert any(action.get("ownAction") for action in seen[2]["recentActions"])
    assert all(
        entry["seq"] <= seen[2]["stateVersion"] for entry in seen[2]["recentActions"]
    )
    public = client.get(url + "/replay")
    assert "private-test-rationale" not in public.text
    assert public.json()["frames"][-1]["event"]["fallback"] is True
    assert client.get(url + "/llm-review").status_code == 409

    # Existing games keep their model identity. Losing the config still allows
    # the owner to end and export a game.
    while view["decision"]:
        view = human_step(client, url, view)
    config.unlink()
    assert client.post(url + "/advance").json()["code"] == "LLM_CONFIG_MISMATCH"
    assert (
        client.post(
            url + "/resign", json={"expectedStateVersion": view["stateVersion"]}
        ).status_code
        == 200
    )
    stranger = TestClient(app)
    stranger.post("/api/battle/session")
    assert stranger.get(url + "/llm-review").status_code == 404
    exported = client.get(url + "/llm-review")
    assert exported.status_code == 200
    assert exported.headers["Content-Disposition"].startswith("attachment")
    report = exported.json()
    assert report["summary"]["requestCount"] == 5
    assert report["summary"]["retryCount"] == 3
    assert report["summary"]["recoveredActions"] == 1
    assert report["summary"]["requestsWithoutUsage"] == 4
    model, fallback = [step for step in report["decisions"] if step["requestCount"]]
    assert model["status"] == "model" and model["requestCount"] == 2
    assert model["reason"] == "private-test-rationale"
    assert model["usage"]["total_tokens"] == 30
    assert model["attempts"][0]["failureType"] == "RemoteProtocolError"
    assert "failure" not in model["attempts"][1]
    assert fallback["requestCount"] == 3
    assert fallback["failure"] == "http-429"
    assert report["status"] == "resigned"
    for secret in (
        "provider-private",
        "example-secret-for-test",
        "provider.example",
        '"seed"',
    ):
        assert secret not in exported.text
    app.state.battles.close()
    assert sessions[0].is_closed


@pytest.mark.parametrize(
    ("status", "header", "wait"),
    [(401, None, None), (404, None, None), (429, "30", None), (503, "3", 3)],
)
def test_llm_retry_budget_and_permanent_http_errors(monkeypatch, status, header, wait):
    attempts = []
    waits = []
    monkeypatch.setattr(llm, "sleep", waits.append)

    def request(*_):
        attempts.append(True)
        if len(attempts) > 1:
            return {"choices": []}
        response = httpx.Response(
            status,
            headers={"Retry-After": header} if header else {},
            request=httpx.Request("POST", "http://localhost/v1/chat/completions"),
        )
        response.raise_for_status()

    monkeypatch.setattr(llm, "_request", request)
    config = llm.Settings(baseUrl="http://localhost/v1", model="test")
    result, trace, error = llm._request_with_retries(config, {})
    assert trace[0]["failure"] == f"http-{status}"
    if wait is None:
        assert waits == [] and len(trace) == 1 and result is None
        assert error == f"http-{status}"
    else:
        assert waits == [wait] and len(trace) == 2
        assert result == {"choices": []} and error is None


def test_llm_discards_in_flight_choice_when_match_ends(tmp_path, monkeypatch):
    app, client, view, _ = setup(tmp_path, monkeypatch)
    service = app.state.battles
    owner = service.owner(client.cookies.get("ptcg_practice_session"))
    match_id = view["matchId"]
    url = "/api/battle/matches/" + match_id
    while view["decision"]:
        view = human_step(client, url, view)
    entered, release = Event(), Event()
    original = llm.decide

    def wait_for_result(*args):
        entered.set()
        assert release.wait(5)
        monkeypatch.setattr(llm, "_request", lambda *_: {"choices": []})
        return original(*args)

    monkeypatch.setattr(llm, "decide", wait_for_result)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.advance, owner, match_id)
        try:
            assert entered.wait(5)
            assert (
                service.advance(owner, match_id)["stateVersion"] == view["stateVersion"]
            )
            assert (
                service.resign(owner, match_id, view["stateVersion"])["status"]
                == "resigned"
            )
        finally:
            release.set()
        assert pending.result(timeout=5)["status"] == "resigned"
    assert service.llm_review(owner, match_id)["decisions"] == []


@pytest.mark.parametrize(
    ("finish", "content", "failure"),
    [
        ("stop", '{"choice":{"optionId":"stale"}}', "illegal-choice"),
        ("length", "", "output-limit"),
    ],
)
def test_llm_rejects_invalid_choice_and_incomplete_output(
    monkeypatch, finish, content, failure
):
    view = {
        "stateVersion": 8,
        "observation": {"self": {"hand": []}, "opponent": {"hand": None}},
        "decision": {
            "id": "d8",
            "kind": "options",
            "options": [
                {"id": "d8:o0", "actionType": "PassTurn"},
                {"id": "d8:o1", "actionType": "PlayPokemonAction"},
            ],
        },
    }
    config = llm.Settings(baseUrl="http://localhost:1234/v1", model="test-model")
    monkeypatch.setattr(
        llm,
        "_request",
        lambda *_: {
            "choices": [
                {"finish_reason": finish, "message": {"content": content}},
            ]
        },
    )
    monkeypatch.setattr(
        llm.h2, "decide", lambda _: ({"choice": {"optionId": "d8:o1"}}, {})
    )
    command, detail = llm.decide(view, [], config, [])
    assert command["choice"] == {"optionId": "d8:o1"}
    assert detail["status"] == "fallback"
    assert detail["failure"] == failure


def test_llm_settings_save_and_connection_test(tmp_path, monkeypatch):
    for name in llm.SETTINGS_ENV.values():
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path / "app.sqlite")) as client:
        settings_url = "/api/battle/llm/settings"
        test_url = "/api/battle/llm/test"
        assert client.get(settings_url).status_code == 401
        assert client.post("/api/battle/session").status_code == 200
        assert client.get(settings_url).json()["settings"] is None

        draft = {
            "baseUrl": "https://provider.example/v1",
            "model": "test-model",
            "apiKey": "test-provider-secret",
        }
        called = []

        def request(config, visible):
            called.append((config.apiKey.get_secret_value(), visible))
            assert config.maxRetries == 0
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"choice":{"optionId":"d0:o0"}}',
                        },
                    }
                ]
            }

        monkeypatch.setattr(llm, "_request", request)
        result = client.post(test_url, json=draft)
        assert result.json()["ok"] is True
        assert client.get(settings_url).json()["settings"] is None
        assert called[0][0] == "test-provider-secret"
        assert not called[0][1]["ownDeck"]

        result = client.post(settings_url, json=draft)
        assert result.status_code == 200 and result.json()["hasApiKey"]
        assert "test-provider-secret" not in result.text
        assert (
            client.post("/api/battle/session").json()["llmOpponent"]["name"]
            == "LLM · test-model"
        )

        revised = {**draft, "model": "replacement", "apiKey": ""}
        assert client.post(settings_url, json=revised).status_code == 200
        assert client.post(test_url, json=revised).json()["ok"] is True
        assert called[-1][0] == "test-provider-secret"
        assert (
            client.post(
                test_url, json={**revised, "baseUrl": "https://other.example/v1"}
            ).json()["code"]
            == "LLM_KEY_REQUIRED"
        )
        assert (
            client.post(
                settings_url,
                json={**draft, "baseUrl": "https://secret@provider.example/v1"},
            ).status_code
            == 422
        )

        def rejected(config, visible):
            req = httpx.Request("POST", config.baseUrl)
            httpx.Response(401, request=req, text="provider-private").raise_for_status()

        monkeypatch.setattr(llm, "_request", rejected)
        result = client.post(test_url, json=revised)
        assert result.json()["failure"] == "http-401"
        assert "provider-private" not in result.text

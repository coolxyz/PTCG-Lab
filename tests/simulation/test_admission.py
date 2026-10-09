import copy
import json
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.collection.domain import RAW, CARDS, validation
from packages.simulation.registry import RELEASE, VERSION
from packages.battle.runtime import ENGINE_VERSION
from scripts.simulation.combinations import variants


def test_all_release_entries_are_real_and_bound_to_engine():
    assert RELEASE["engineVersion"] == ENGINE_VERSION
    assert len(RELEASE["effects"]) >= 33
    for row in RELEASE["effects"]:
        assert row["mechanisms"] and row["testSuites"]
        assert all(CARDS[p]["engineId"] == row["effectKey"] for p in row["printings"])


def test_both_custom_revisions_admitted_and_pinned(tmp_path):
    app = create_app(tmp_path / "test.sqlite")
    client = TestClient(app)
    client.post("/api/battle/session", json={})
    ids = []
    for i, v in enumerate([variants()[1], variants()[-1]]):
        deck = app.state.store.create_deck(f"custom-{i}", v["entries"])
        r = app.state.store.freeze(deck["id"], deck["version"])
        ids.append(r["id"])
        assert r["effectReleaseVersion"] == VERSION
    request = {
        "requestId": "custom-composition-001",
        "revisionId": ids[0],
        "opponentRevisionId": ids[1],
    }
    response = client.post("/api/battle/matches", json=request)
    assert response.status_code == 201, response.text
    mid = response.json()["matchId"]
    assert client.post("/api/battle/matches", json=request).json()["matchId"] == mid
    assert (
        client.post(
            "/api/battle/matches", json={**request, "opponent": "dragapult"}
        ).status_code
        == 422
    )
    with app.state.store.db() as db:
        record = json.loads(
            db.execute("select body from matches where id=?", (mid,)).fetchone()[0]
        )
    assert record["config"]["provenance"]["effectReleaseVersion"] == VERSION
    assert record["config"]["provenance"]["opponentRevisionId"] == ids[1]
    # Later edits to the original draft do not change the frozen opponent config.
    before = copy.deepcopy(record["config"]["deck2"])
    app.state.store.save_deck(
        deck["id"], "edited", RAW["templates"][0]["entries"], deck["version"]
    )
    with app.state.store.db() as db:
        after = json.loads(
            db.execute("select body from matches where id=?", (mid,)).fetchone()[0]
        )
    assert after["config"]["deck2"] == before


def test_unsupported_printing_is_named_in_blockers():
    entries = copy.deepcopy(RAW["templates"][0]["entries"])
    unknown = next(c for c in CARDS.values() if c["effectStatus"] != "verified")
    entries[0]["quantity"] -= 1
    entries = [e for e in entries if e["quantity"]]
    entries.append({"printingId": unknown["printingId"], "quantity": 1})
    result = validation(entries)
    assert not result["playable"]
    assert any(unknown["printingId"] in issue["cardRefs"] for issue in result["issues"])


def test_structured_animation_events_never_expose_hand_locations():
    from packages.battle.service import MatchService

    dto = {
        "phase": "playing",
        "decision": {
            "kind": "options",
            "options": [
                {
                    "id": "x",
                    "actionType": "AttachEnergyAction",
                    "source": "Metal Energy",
                    "sourceRef": "v7:self:hand:3",
                    "targetRef": "v7:self:active:0",
                }
            ],
        },
    }
    event = MatchService._event(dto, {"choice": {"optionId": "x"}}, ai=True)
    assert event["targetLocation"] == "opponent:active:0"
    assert "sourceLocation" not in event
    assert "hand" not in json.dumps(event)

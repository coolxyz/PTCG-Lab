import copy
import json

import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.collection.domain import CARDS, RAW, validation
from packages.battle.agent import predict
from packages.battle.service import MatchService
from scripts.cardpool.reprints import ROOT, read


def substitute(template, offset=0):
    entries = copy.deepcopy(template["entries"])
    for entry in entries:
        effect = CARDS[entry["printingId"]]["engineId"]
        reprints = sorted(
            c["printingId"]
            for c in CARDS.values()
            if c.get("reprintAnchor") and c["engineId"] == effect
        )
        if reprints:
            entry["printingId"] = reprints[offset % len(reprints)]
    return entries


def test_all_reprints_admit_equivalent_decks_and_keep_same_name_limit():
    template_effects={CARDS[e['printingId']]['engineId'] for t in RAW['templates'] for e in t['entries']}
    reviews=[{'printingId':c['printingId'],'effectKey':c['engineId']} for c in CARDS.values() if c['sourceVerified'] and c['effectStatus']=='verified' and c['engineId'] in template_effects and (c.get('mark') in 'GHIJ' if c.get('mark') else bool(c.get('basicEnergyType')))]
    assert len(reviews)>100
    for review in reviews:
        pid, effect = review["printingId"], review["effectKey"]
        template = next(
            t
            for t in RAW["templates"]
            if any(CARDS[e["printingId"]]["engineId"] == effect for e in t["entries"])
        )
        entries = copy.deepcopy(template["entries"])
        old = next(e for e in entries if CARDS[e["printingId"]]["engineId"] == effect)
        old["printingId"] = pid
        assert validation(entries)["playable"], pid
        assert MatchService._lines(entries) == MatchService._lines(template["entries"])
    entries = [
        {"printingId": "CN:CSV4C:089", "quantity": 3},
        {"printingId": "CN:CSVM2cC:007", "quantity": 2},
        {"printingId": "CN:CSVM2cC:004", "quantity": 4},
        {"printingId": "CN:CSM2.1C:044", "quantity": 51},
    ]
    assert any(i["code"] == "SAME_NAME_LIMIT" for i in validation(entries)["issues"])


@pytest.mark.parametrize("seed,offset", [(7, 0), (23, 3)])
def test_reprint_decks_finish_a1_game_and_survive_service_restart(
    tmp_path, monkeypatch, seed, offset
):
    monkeypatch.setattr("packages.battle.service.secrets.randbits", lambda _: seed)
    app = create_app(tmp_path / "reprints.sqlite")
    client = TestClient(app)
    client.post("/api/battle/session", json={})
    ids = []
    for template in RAW["templates"]:
        deck = app.state.store.create_deck("重印自由组牌", substitute(template, offset))
        ids.append(app.state.store.freeze(deck["id"], deck["version"])["id"])
    response = client.post(
        "/api/battle/matches",
        json={
            "requestId": "p4-reprints-create",
            "revisionId": ids[0],
            "opponentRevisionId": ids[1],
            "aiLevel": "A1",
        },
    )
    assert response.status_code == 201, response.text
    view = response.json()
    service = app.state.battles
    owner = service.owner(client.cookies.get("ptcg_practice_session"))
    for step in range(1500):
        if view["done"]:
            break
        if step % 40 == 0:
            service = MatchService(app.state.store)
        if view["decision"]:
            command, _ = predict(view)
            command["commandId"] = f"p4-reprint-command-{step}"
            view = service.command(owner, view["matchId"], command)["view"]
        else:
            view = service.advance(owner, view["matchId"])
    assert view["done"] and view["status"] == "finished"
    assert view["result"]["reason"] in ("prizes_taken", "no_pokemon", "deck_out")


def test_old_engine_effect_release_is_not_resumed_under_new_rules(
    tmp_path, monkeypatch
):
    from packages.battle import runtime
    from packages.battle.agent import VERSION as AI_VERSION
    from packages.simulation.registry import VERSION
    from packages.collection.domain import DomainError

    from packages.simulation import registry as releases
    import hashlib
    historical=copy.deepcopy(releases.RELEASE)
    historical['engineVersion']='historical-engine-fixture'
    raw=json.dumps(historical).encode()
    old=hashlib.sha256(raw).hexdigest()
    archive=tmp_path/'data/cardpool/release-history'
    archive.mkdir(parents=True)
    (archive/(old+'.json')).write_bytes(raw)
    monkeypatch.setattr(releases,'PATH',tmp_path/'data/simulation/effects.json')
    assert old != VERSION
    assert (
        read(archive / (old + ".json"))["engineVersion"]
        != runtime.ENGINE_VERSION
    )
    app = create_app(tmp_path / "historical.sqlite")
    game = runtime.Adapter(
        7,
        MatchService._lines(RAW["templates"][0]["entries"]),
        MatchService._lines(RAW["templates"][1]["entries"]),
        provenance={"effectReleaseVersion": old, "aiVersion": AI_VERSION},
    )
    record = game.export_private_replay()
    service = app.state.battles
    with pytest.raises(DomainError) as error:
        service._game(
            {
                "body": json.dumps(record),
                "id": "historical",
                "seq": game.version,
                "engine_version": runtime.ENGINE_VERSION,
            }
        )
    assert error.value.code == "EFFECT_RELEASE_MISMATCH"
    assert record == game.export_private_replay()

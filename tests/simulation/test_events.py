import copy
from packages.simulation.events import public_effects


def test_only_public_counts_and_hp_enter_effects():
    side = {
        "hand_count": 4,
        "deck_count": 30,
        "prize_count": 6,
        "active": [{"name": "Public", "hp": 100}],
        "bench": [],
        "discard": [],
        "lost_zone": [],
    }
    before = {"self": copy.deepcopy(side), "opponent": copy.deepcopy(side)}
    after = copy.deepcopy(before)
    after["opponent"].update(hand_count=5, deck_count=29, hand=["PRIVATE CARD"])
    after["self"]["active"][0]["hp"] = -20
    effects = public_effects(before, after)
    assert {
        "kind": "hp",
        "side": "self",
        "zone": "active",
        "index": 0,
        "before": 100,
        "after": 0,
    } in effects
    assert len([e for e in effects if e["kind"] != "move"]) == 3
    assert "PRIVATE" not in str(effects)
    assert public_effects(after, after) == []
    assert public_effects(None, after) == []


def test_replaced_slot_is_not_reported_as_damage():
    before = {
        "self": {
            "hand_count": 1,
            "deck_count": 1,
            "prize_count": 1,
            "active": [{"name": "A", "hp": 100}],
            "bench": [],
            "discard": [],
            "lost_zone": [],
        }
    }
    before["opponent"] = copy.deepcopy(before["self"])
    after = copy.deepcopy(before)
    after["self"]["active"] = [{"name": "B", "hp": 50}]
    assert not any(e["kind"] == "hp" for e in public_effects(before, after))


def test_public_card_movement_attachment_and_hidden_draw():
    side = {
        "hand_count": 2,
        "deck_count": 30,
        "prize_count": 6,
        "active": [{"id": "A", "name": "A", "hp": 100, "attachment": ["A"]}],
        "bench": [],
        "hand": [{"name": "B"}, {"name": "Energy"}],
        "discard": [],
        "lost_zone": [],
    }
    before = {"self": copy.deepcopy(side), "opponent": copy.deepcopy(side)}
    after = copy.deepcopy(before)
    after["self"]["hand"] = []
    after["self"]["hand_count"] = 0
    after["self"]["bench"] = [{"name": "B"}]
    after["self"]["active"][0]["attachment"].append("Energy")
    after["opponent"].update(hand_count=3, deck_count=29, hand=[{"name": "SECRET"}])
    result = public_effects(before, after)
    moves = [e for e in result if e["kind"] == "move"]
    assert any(
        e["cardName"] == "B" and e["zone"] == "bench" and e["fromZone"] == "hand"
        for e in moves
    )
    assert any(e["cardName"] == "Energy" and e["label"] == "附着" for e in moves)
    assert any(
        e["side"] == "opponent" and e["cardName"] is None and e["fromZone"] == "deck"
        for e in moves
    )
    assert "SECRET" not in str(result)


def test_evolution_is_one_movement_and_turn_change_is_explicit():
    side = {
        "hand_count": 1,
        "deck_count": 30,
        "prize_count": 6,
        "active": [{"name": "Basic", "hp": 60}],
        "bench": [],
        "hand": [{"name": "Evolution"}],
        "discard": [],
        "lost_zone": [],
    }
    before = {
        "self": copy.deepcopy(side),
        "opponent": copy.deepcopy(side),
        "turn_number": 1,
        "turn": "PLAYER1",
    }
    after = copy.deepcopy(before)
    after["self"].update(
        hand=[],
        hand_count=0,
        active=[{"name": "Evolution", "hp": 100, "evolved": ["Basic"]}],
    )
    after.update(turn_number=2, turn="PLAYER2")
    result = public_effects(before, after)
    moves = [e for e in result if e["kind"] == "move"]
    assert len(moves) == 1 and moves[0]["label"] == "进化"
    assert any(e["kind"] == "turn" for e in result)

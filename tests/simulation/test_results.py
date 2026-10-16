import copy
import pytest
from packages.simulation.results import decorate


def frame(winner="PLAYER1"):
    return {
        "done": True,
        "status": "finished",
        "winner": winner,
        "observation": {
            "termination_reason": None,
            "self": {"id": "player1", "prize_count": 0, "active": [{}], "bench": []},
            "opponent": {
                "id": "player2",
                "prize_count": 6,
                "active": [],
                "bench": [{}],
            },
        },
    }


def test_prizes_take_priority_and_do_not_mutate_engine_observation():
    v = frame()
    obs = copy.deepcopy(v["observation"])
    assert decorate(v)["result"] == {
        "reason": "prizes_taken",
        "conditions": ["prizes_taken"],
    }
    assert v["observation"] == obs


def test_opponent_win_and_no_pokemon_require_confirmed_terminal():
    v = frame("PLAYER2")
    v["observation"]["self"].update(prize_count=2, active=[], bench=[])
    assert decorate(v)["result"]["reason"] == "no_pokemon"
    v["done"] = False
    assert decorate(v)["result"] is None


@pytest.mark.parametrize("status", ["resigned", "truncated"])
def test_service_endings_override_board_conditions(status):
    v = frame()
    v["status"] = status
    assert decorate(v)["result"]["reason"] == status


def test_deckout_and_unknown_are_not_guessed_from_empty_active():
    v = frame()
    v["observation"]["termination_reason"] = "deck_out"
    assert decorate(v)["result"]["reason"] == "deck_out"
    v = frame()
    v["observation"]["self"]["prize_count"] = 1
    assert decorate(v)["result"]["reason"] == "unknown"


@pytest.mark.parametrize("own_prizes,other_prizes,own_field,other_field,draw", [
    (0, 0, True, True, True),
    (2, 2, False, False, True),
    (0, 2, False, True, True),
    (0, 0, False, False, True),
    (0, 0, True, False, False),
    (2, 2, True, True, False),
])
def test_only_proven_equal_simultaneous_conditions_are_draws(own_prizes, other_prizes, own_field, other_field, draw):
    v = frame(None)
    v["observation"]["termination_reason"] = "simultaneous_knockout"
    for side, prizes, field in (("self", own_prizes, own_field), ("opponent", other_prizes, other_field)):
        v["observation"][side].update(prize_count=prizes, active=[{}] if field else [], bench=[])
    assert (decorate(v)["result"].get("outcome") == "draw") == draw
    v["status"] = "truncated"
    assert decorate(v)["result"]["reason"] == "truncated"
    v["status"] = "finished"
    v["done"] = False
    assert decorate(v)["result"] is None


def test_unknown_null_winner_is_not_a_draw():
    v = frame(None)
    v["observation"]["opponent"]["prize_count"] = 0
    assert decorate(v)["result"]["reason"] == "unknown"

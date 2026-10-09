import copy
import threading
import pytest
from packages.battle.runtime import Adapter, view
from packages.simulation.information import InformationSet, observation
from packages.simulation.a2 import SearchBudget, decide, legal
from packages.simulation.search import search, candidates


def information():
    from packages.collection.domain import RAW
    from packages.battle.service import MatchService

    game = Adapter(29, *[MatchService._lines(t["entries"]) for t in RAW["templates"][:2]])
    player = game.actor
    own, other = (
        (game.config["deck1"], game.config["deck2"])
        if player.name == "PLAYER1"
        else (game.config["deck2"], game.config["deck1"])
    )
    return game, InformationSet(
        player.name, [observation(view(game, player))], {}, own, [other]
    )


def test_information_search_is_reproducible_legal_and_budgeted():
    game, info = information()
    before, original = game.private_digest(), copy.deepcopy(info)
    budget = SearchBudget(
        seconds=5, particles=2, trials=12, depth=3, candidates=4, simulations=4
    )
    result = search(info, budget, sampling_seed=91)
    again = search(copy.deepcopy(info), budget, sampling_seed=91)
    assert result == again
    assert result["status"] == "searched" and 0 < result["simulations"] <= 4
    assert legal(info.observations[-1], result["command"])
    assert game.private_digest() == before and info == original


def test_worker_returns_search_result_and_timeout_returns_legal_fallback():
    game, info = information()
    result = decide(
        info, SearchBudget(seconds=5, particles=1, trials=8, depth=2, simulations=8)
    )
    assert result["stopReason"] == "complete" and result["status"] == "searched"
    assert legal(info.observations[-1], result["command"])
    result = decide(info, SearchBudget(seconds=0.001))
    assert result["stopReason"] == "timeout" and result["seconds"] < 1
    assert legal(info.observations[-1], result["command"])
    game.submit(game.actor, result["command"])


def test_cancellation_and_hidden_information_rejected():
    _, info = information()
    cancelled = threading.Event()
    timer = threading.Timer(0.05, cancelled.set)
    timer.start()
    try:
        result = decide(
            info, SearchBudget(seconds=10, trials=256, particles=256), cancel=cancelled
        )
    finally:
        timer.join()
    assert result.get("stopReason", result["status"]) == "cancelled"
    assert legal(info.observations[-1], result["command"])
    info.observations[0]["seed"] = 29
    with pytest.raises(ValueError, match="PRIVATE_DATA"):
        decide(info)


@pytest.mark.parametrize(
    "field,value",
    [
        ("seconds", 0),
        ("depth", 100),
        ("particles", 0),
        ("simulations", 1),
        ("memory_mb", 64),
    ],
)
def test_invalid_search_budget(field, value):
    with pytest.raises(ValueError, match="INVALID_SEARCH_BUDGET"):
        SearchBudget(**{field: value}).validate()


def test_candidate_cap_does_not_expand_exponential_selections():
    dto = {
        "stateVersion": 0,
        "phase": "playing",
        "observation": {
            "self": {"hand": [], "active": [], "bench": []},
            "opponent": {"hand": None, "active": []},
        },
        "decision": {
            "id": "d0",
            "kind": "selection",
            "min": 30,
            "max": 30,
            "hidden": True,
            "candidates": [{"ref": str(i)} for i in range(60)],
        },
    }
    result = candidates(dto, 8)
    assert len(result) <= 8
    assert all(len(set(c["selectedRefs"])) == 30 for c in result)


def test_busy_and_unavailable_worker_fall_back_without_spawning(monkeypatch):
    import packages.simulation.a2 as a2

    _, info = information()
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise OSError("cannot start worker")

    monkeypatch.setattr(a2.subprocess, "Popen", unavailable)
    assert a2._CAPACITY.acquire(blocking=False)
    try:
        result = decide(info)
        assert result["stopReason"] == "busy" and not calls
        assert legal(info.observations[-1], result["command"])
    finally:
        a2._CAPACITY.release()
    result = decide(info)
    assert result["stopReason"] == "worker_unavailable" and len(calls) == 1
    assert legal(info.observations[-1], result["command"])


def test_malformed_worker_choices_are_rejected():
    _, info = information()
    root = info.observations[-1]
    for choice in (None, [], {"optionId": []}, {"selectedRefs": [None]}):
        assert not legal(
            root,
            {
                "expectedStateVersion": root["stateVersion"],
                "decisionId": root["decision"]["id"],
                "choice": choice,
            },
        )

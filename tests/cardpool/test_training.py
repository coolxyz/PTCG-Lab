import copy
import pytest
from packages.battle.runtime import Adapter, view
from packages.battle.decision import decide
from packages.cardpool.training import record, finish


def test_training_records_are_actor_only_detached_and_action_validated():
    game = Adapter(17)
    dto = view(game, game.actor)
    command, _ = decide(dto)
    item = record(
        dto, command, own_deck=[], versions={"rules": "test"}, policy_version="test"
    )
    assert item["features"]["observation"]["opponent"]["hand"] is None
    assert "seed" not in item and "config" not in item
    dto["observation"]["self"]["deck_count"] = -1
    assert item["features"]["observation"] != dto["observation"]
    assert finish([item], item["actor"])[0]["outcome"] == 1
    command["choice"] = {"optionId": "fake"}
    with pytest.raises(ValueError, match="ILLEGAL_TRAINING_ACTION"):
        record(dto, command, own_deck=[], versions={}, policy_version="test")


@pytest.mark.parametrize("field", ["seed", "rngState", "opponentDeck", "privateReplay"])
def test_training_rejects_private_state_in_nested_input(field):
    game = Adapter(18)
    dto = view(game, game.actor)
    command, _ = decide(dto)
    leaked = copy.deepcopy(dto)
    leaked["observation"]["extra"] = {field: "secret"}
    with pytest.raises(ValueError, match="PRIVATE_TRAINING_FIELD"):
        record(leaked, command, own_deck=[], versions={}, policy_version="test")

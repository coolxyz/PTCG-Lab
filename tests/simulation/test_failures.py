import json

import pytest

from packages.battle.runtime import Adapter, view
from packages.battle.agent import predict
from packages.simulation.failures import capture, reproduce


def test_private_bundle_replays_original_failure_and_minimizes_prefix(tmp_path):
    game = Adapter(19)
    commands = []
    for _ in range(20):
        cmd = predict(view(game, game.actor))[0]
        commands.append({"actor": game.actor.name, "command": cmd})
        game.submit(game.actor, cmd)

    # A deterministic injected property failure exercises the real replay path.
    def property_check(state):
        if state.turn_number >= 2:
            raise AssertionError("INJECTED_PROPERTY_FAILURE")

    path = tmp_path / "failure.json"
    result = capture(
        path,
        game.config,
        commands,
        AssertionError("INJECTED_PROPERTY_FAILURE"),
        property_check,
    )
    assert result["reproducible"] and result["complete"]
    assert 0 < result["minimalSteps"] <= len(commands)
    with pytest.raises(AssertionError, match="INJECTED_PROPERTY_FAILURE"):
        reproduce(path, property_check)
    data = json.loads(path.read_text(encoding="utf-8"))
    from packages.simulation.failures import replay

    replay(data["config"], data["minimization"]["commands"][:-1], property_check)
    data["engineVersion"] = "different"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="VERSION_MISMATCH"):
        reproduce(path)

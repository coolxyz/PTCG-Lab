"""Trusted rule continuation tests. No full private state is passed to an agent."""

import pytest
from packages.battle.runtime import Adapter, view
from packages.battle.agent import predict
from packages.simulation.fork import RuleFork, Continuation
from packages.battle.service import MatchService
from scripts.simulation.combinations import variants


@pytest.mark.parametrize("deck_index", [0, 12, 24])
def test_fork_every_observed_pause_matches_original_and_does_not_mutate_parent(
    deck_index,
):
    decks = variants()
    branch = RuleFork(
        Adapter(
            8123,
            MatchService._lines(decks[deck_index]["entries"]),
            MatchService._lines(decks[(deck_index + 7) % len(decks)]["entries"]),
        )
    )
    kinds = set()
    for step in range(500):
        game = branch.game
        if game.done:
            break
        dto = view(game, game.actor)
        kinds.add((game.env.phase, dto["decision"]["kind"]))
        command = predict(dto)[0]
        child = RuleFork.restore(Continuation.from_json(branch.checkpoint().to_json()))
        digest = game.private_digest()
        assert view(child.game, child.game.actor) == dto
        child.submit(child.game.actor, command)
        assert game.private_digest() == digest
        branch.submit(game.actor, command)
        assert child.game.private_digest() == game.private_digest()
        if not game.done:
            assert view(child.game, child.game.actor) == view(game, game.actor)
    assert branch.game.done
    assert ("playing", "selection") in kinds and ("playing", "options") in kinds


def test_version_mismatch_rejected():
    token = RuleFork(Adapter(7)).checkpoint()
    token.engine_version = "unknown"
    with pytest.raises(ValueError, match="ENGINE_VERSION_MISMATCH"):
        RuleFork.restore(token)


def test_sibling_choices_and_rng_are_isolated():
    branch = RuleFork(Adapter(9))
    for _ in range(50):
        if branch.regular(branch.game) and len(branch.game.actions) > 1:
            break
        branch.submit(
            branch.game.actor, predict(view(branch.game, branch.game.actor))[0]
        )
    parent = branch.game.private_digest()
    a, b = branch.fork(), branch.fork()
    dto = view(a.game, a.game.actor)
    cmd = predict(dto)[0]
    alternate = next(
        o["id"]
        for o in dto["decision"]["options"]
        if o["id"] != cmd["choice"]["optionId"]
    )
    a.submit(a.game.actor, cmd)
    cmd["choice"] = {"optionId": alternate}
    b.submit(b.game.actor, cmd)
    assert branch.game.private_digest() == parent
    assert a.game.private_digest() != b.game.private_digest()
    original_rng = branch.game.env.rng.getstate()
    a.game.env.rng.random()
    assert branch.game.env.rng.getstate() == original_rng

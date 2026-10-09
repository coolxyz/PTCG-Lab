import copy
import json
import random

import pytest
from loguru import logger
from ptcg.core.enums import PlayerId
from packages.engine_adapter.base import Adapter, RejectedCommand, policy

logger.remove()


def test_json_roundtrip_and_no_engine_objects():
    game = Adapter(42)
    view = game.view(game.actor)
    assert json.loads(json.dumps(view)) == view
    assert "seed" not in view and "full_state" not in view and "raw_available_actions" not in view
    assert view["observation"]["opponent"]["hand"] is None
    assert "deck" not in view["observation"]["self"]
    assert "prize" not in view["observation"]["self"]


def test_return_value_is_detached():
    game = Adapter(42)
    before = game.private_digest()
    view = game.view(game.actor)
    view["observation"]["self"]["hand"].clear()
    view["decision"]["candidates"].clear()
    assert game.private_digest() == before


def test_idempotency_and_conflicts():
    game = Adapter(42)
    actor = game.actor
    command = policy(game.view(actor), random.Random(1))
    receipt = game.submit(actor, command)
    before = game.private_digest()
    assert game.submit(actor, command) == receipt
    assert game.private_digest() == before and game.version == 1
    altered = copy.deepcopy(command)
    altered["choice"] = {}
    with pytest.raises(RejectedCommand, match="IDEMPOTENCY_CONFLICT"):
        game.submit(actor, altered)


@pytest.mark.parametrize("mutation", ["wrong_actor", "stale", "bad_ref", "duplicate_ref"])
def test_rejections_do_not_mutate_rng_or_state(mutation):
    game = Adapter(42)
    actor = game.actor
    command = policy(game.view(actor), random.Random(1))
    if mutation == "wrong_actor":
        actor = PlayerId.PLAYER2
    if mutation == "stale":
        command["expectedStateVersion"] = -1
    if mutation == "bad_ref":
        command["choice"]["selectedRefs"] = ["invalid"]
    if mutation == "duplicate_ref":
        command["choice"]["selectedRefs"] *= 2
    before = game.private_digest()
    with pytest.raises(RejectedCommand):
        game.submit(actor, command)
    assert game.private_digest() == before
    assert game.version == 0


def test_non_actor_has_no_decision():
    game = Adapter(42)
    assert game.view(PlayerId.PLAYER2)["decision"] is None


def test_hidden_zone_permutation_noninterference():
    game = Adapter(42)
    before = game.view(PlayerId.PLAYER1)
    other = game.env.gamestate.player2
    other.hand.reverse()
    other.left.reverse()
    other.prize.reverse()
    # Swap different identities between opponent's hidden zones, preserving counts.
    other.hand[0], other.left[0] = other.left[0], other.hand[0]
    game.env.gamestate.player1.left.reverse()
    game.env.gamestate.player1.prize.reverse()
    assert before == game.view(PlayerId.PLAYER1)


@pytest.mark.parametrize("length", [1, 2, 10, 30])
def test_replay_continuation(length):
    game = Adapter(7)
    rng = random.Random(17)
    for _ in range(length):
        if game.done:
            break
        game.submit(game.actor, policy(game.view(game.actor), rng))
    restored = Adapter.replay(game.export_private_replay())
    assert game.private_digest() == restored.private_digest()
    if not game.done:
        actor = game.actor
        command = policy(game.view(actor), rng)
        game.submit(actor, command)
        restored.submit(actor, command)
        assert game.private_digest() == restored.private_digest()


def test_interleaved_games_are_independent():
    first = Adapter(42)
    second = Adapter(91)
    before = first.private_digest()
    rng = random.Random(9)
    for _ in range(25):
        second.submit(second.actor, policy(second.view(second.actor), rng))
    assert first.private_digest() == before


def test_cannot_deepcopy_live_generator():
    game = Adapter(42)
    with pytest.raises(TypeError, match="generator"):
        copy.deepcopy(game.env)


def test_attack_choice_can_be_serialized():
    from ptcg.core.attack import Attack
    from ptcg.core.action import ChooseCardActionSpace
    game = Adapter(7)
    attack = Attack({"name": "Test move", "damage": 20, "cost": [], "text": "Test"})
    game.info["raw_available_actions"] = ChooseCardActionSpace(
        PlayerId.PLAYER1, PlayerId.PLAYER1, 1, 1, [attack])
    dto = game.view(PlayerId.PLAYER1)
    assert dto["decision"]["candidates"][0]["card"]["kind"] == "attack"

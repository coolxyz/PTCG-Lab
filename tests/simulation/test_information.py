import copy
import pytest
from packages.battle.runtime import Adapter, PlayerId, view
from packages.battle.service import MatchService
from packages.collection.domain import RAW
from packages.simulation.fork import RuleFork
from packages.simulation.information import InformationSet, observation, sample


def fixture():
    decks = [MatchService._lines(t["entries"]) for t in RAW["templates"][:2]]
    game = Adapter(99, *decks)
    info = InformationSet(
        "PLAYER1", [observation(view(game, PlayerId.PLAYER1))], {}, decks[0], [decks[1]]
    )
    return game, info


def test_independent_particles_match_all_evidence_without_mutating_input():
    game, info = fixture()
    original = copy.deepcopy(info)
    before = game.private_digest()
    result = sample(info, sampling_seed=71, trials=40, count=3)
    assert result["status"] == "ok"
    digests = set()
    for token in result["particles"]:
        world = RuleFork.restore(token).game
        assert observation(view(world, PlayerId.PLAYER1)) == info.observations[0]
        digests.add(world.private_digest())
    assert len(digests) > 1
    assert game.private_digest() == before and info == original
    again = sample(copy.deepcopy(info), sampling_seed=71, trials=40, count=3)
    assert [p.to_json() for p in result["particles"]] == [
        p.to_json() for p in again["particles"]
    ]


def test_unsatisfiable_history_exhausts_instead_of_using_real_hidden_state():
    _, info = fixture()
    info.observations[0]["observation"]["self"]["hand_count"] = 999
    result = sample(info, sampling_seed=71, trials=4)
    assert result == {"status": "exhausted", "proposals": 4, "particles": []}


@pytest.mark.parametrize("key", ["seed", "rng", "checkpoint", "config"])
def test_private_state_cannot_enter_information_set(key):
    _, info = fixture()
    info.observations[0][key] = "secret"
    with pytest.raises(ValueError, match="PRIVATE_DATA"):
        sample(info, sampling_seed=71)


def test_opponent_hand_and_incomplete_history_rejected():
    _, info = fixture()
    info.observations[0]["observation"]["opponent"]["hand"] = []
    with pytest.raises(ValueError, match="OPPONENT_HAND"):
        info.validate()
    _, info = fixture()
    info.observations[0]["stateVersion"] = 5
    with pytest.raises(ValueError, match="INCOMPLETE"):
        info.validate()


def test_unknown_deck_prior_is_not_silently_accepted():
    _, info = fixture()
    info.opponent_priors[0].append("1 Unknown UNKNOWN 999")
    with pytest.raises(ValueError, match="UNSUPPORTED_DECK"):
        info.validate()


@pytest.mark.parametrize("seed", [17, 99])
@pytest.mark.parametrize("viewer_name", ["PLAYER1", "PLAYER2"])
def test_conditioned_opening_includes_only_observed_hand_and_public_mulligans(
    seed, viewer_name
):
    from packages.battle.agent import predict
    from packages.simulation.fork import Continuation

    decks = [MatchService._lines(t["entries"]) for t in RAW["templates"][:2]]
    game = Adapter(seed, *decks)
    viewer = PlayerId[viewer_name]
    snapshots = [observation(view(game, viewer))]
    command = predict(view(game, game.actor))[0]
    choices = {"0": command["choice"]} if game.actor == viewer else {}
    game.submit(game.actor, command)
    snapshots.append(observation(view(game, viewer)))
    own_index = 0 if viewer_name == "PLAYER1" else 1
    info = InformationSet(
        viewer_name, snapshots, choices, decks[own_index], [decks[1 - own_index]]
    )
    result = sample(info, sampling_seed=791, trials=100, count=2)
    assert result["status"] == "ok", result
    for token in result["particles"]:
        branch = RuleFork.restore(Continuation.from_json(token.to_json()))
        assert observation(view(branch.game, viewer)) == snapshots[-1]
        for _ in range(25):
            twin = RuleFork.restore(
                Continuation.from_json(branch.checkpoint().to_json())
            )
            cmd = predict(view(branch.game, branch.game.actor))[0]
            branch.submit(branch.game.actor, cmd)
            twin.submit(twin.game.actor, cmd)
            assert branch.game.private_digest() == twin.game.private_digest()


def test_incompatible_but_legal_opponent_prior_is_rejected_without_aborting_sampler():
    from packages.battle.agent import predict

    decks = [MatchService._lines(t["entries"]) for t in RAW["templates"][:2]]
    game = Adapter(99, *decks)
    observations = [observation(view(game, PlayerId.PLAYER1))]
    command = predict(view(game, game.actor))[0]
    choices = {"0": command["choice"]} if game.actor == PlayerId.PLAYER1 else {}
    game.submit(game.actor, command)
    observations.append(observation(view(game, PlayerId.PLAYER1)))
    info = InformationSet(
        "PLAYER1", observations, choices, decks[0], [decks[0], decks[1]]
    )
    result = sample(info, sampling_seed=11, trials=100)
    assert result["status"] == "ok"


def test_twelve_step_public_history_regression_and_independent_worlds():
    """Current source-owned reference history, retaining the 100-proposal ceiling."""
    from packages.battle.agent import predict
    from scripts.simulation.combinations import variants
    from packages.simulation.fork import Continuation

    decks = [MatchService._lines(t['entries']) for t in RAW['templates'][:2]]
    game = Adapter(610001, *decks)
    snapshots, choices = [], {}
    for step in range(13):
        snapshots.append(observation(view(game, PlayerId.PLAYER1)))
        if step == 12:
            break
        command = predict(view(game, game.actor))[0]
        if game.actor == PlayerId.PLAYER1:
            choices[str(step)] = command["choice"]
        game.submit(game.actor, command)
    info = InformationSet("PLAYER1", snapshots, choices, decks[0], [decks[1]])
    before = game.private_digest()
    result = sample(info, sampling_seed=710001, trials=100, count=2)
    assert result["status"] == "ok"
    signatures = set()
    for token in result["particles"]:
        branch = RuleFork.restore(Continuation.from_json(token.to_json()))
        assert observation(view(branch.game, PlayerId.PLAYER1)) == snapshots[-1]
        signatures.add(branch.game.private_digest())
        for _ in range(20):
            command = predict(view(branch.game, branch.game.actor))[0]
            twin = RuleFork.restore(
                Continuation.from_json(branch.checkpoint().to_json())
            )
            branch.submit(branch.game.actor, command)
            twin.submit(twin.game.actor, command)
            assert branch.game.private_digest() == twin.game.private_digest()
    assert len(signatures) == 2 and game.private_digest() == before


@pytest.mark.parametrize("viewer", [PlayerId.PLAYER1, PlayerId.PLAYER2])
def test_32_step_draw_history_in_both_information_sets(viewer):
    from packages.battle.agent import predict

    deck = MatchService._lines([{'printingId':'CN:CSVM2cC:004','quantity':4}, {'printingId':'CN:CSM2.1C:044','quantity':56}])
    game = Adapter(0, deck, deck)
    snapshots, choices = [], {}
    for step in range(33):
        snapshots.append(observation(view(game, viewer)))
        if step == 32:
            break
        dto = view(game, game.actor)
        command = predict(dto)[0]
        if game.env.phase == "playing":
            command["choice"] = {
                "optionId": next(
                    o["id"]
                    for o in dto["decision"]["options"]
                    if o["actionType"] == "PassTurn"
                )
            }
        if game.actor == viewer:
            choices[str(step)] = command["choice"]
        game.submit(game.actor, command)
    info = InformationSet(viewer.name, snapshots, choices, deck, [deck])
    result = sample(info, sampling_seed=9831, trials=100, count=2)
    assert result["status"] == "ok"
    assert (
        len({RuleFork.restore(p).game.private_digest() for p in result["particles"]})
        == 2
    )
    for token in result["particles"]:
        assert observation(view(RuleFork.restore(token).game, viewer)) == snapshots[-1]


@pytest.mark.parametrize("viewer", [PlayerId.PLAYER1, PlayerId.PLAYER2])
def test_repeated_mulligans_condition_every_observed_deal(viewer):
    from packages.collection.domain import RAW
    from packages.battle.agent import predict
    from packages.simulation.fork import Continuation

    decks = [MatchService._lines(t["entries"]) for t in RAW["templates"][:2]]
    game = Adapter(510000, *decks)
    snapshots, choices = [], {}
    for step in range(10):
        snapshots.append(observation(view(game, viewer)))
        if step == 9:
            break
        cmd = predict(view(game, game.actor))[0]
        if game.actor == viewer:
            choices[str(step)] = cmd["choice"]
        game.submit(game.actor, cmd)
    assert sum(e["kind"] == "mulligan" for e in snapshots[-1]["setupEvents"]) >= 2
    before = game.private_digest()
    info = InformationSet(
        viewer.name,
        snapshots,
        choices,
        decks[0 if viewer == PlayerId.PLAYER1 else 1],
        decks,
    )
    sampled = sample(info, sampling_seed=12, trials=100, count=2)
    assert sampled["status"] == "ok"
    for token in sampled["particles"]:
        branch = RuleFork.restore(Continuation.from_json(token.to_json()))
        assert observation(view(branch.game, viewer)) == snapshots[-1]
        twin = branch.fork()
        cmd = predict(view(branch.game, branch.game.actor))[0]
        branch.submit(branch.game.actor, cmd)
        twin.submit(twin.game.actor, cmd)
        assert branch.game.private_digest() == twin.game.private_digest()
    assert game.private_digest() == before

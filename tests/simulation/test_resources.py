"""Public synthetic regressions for the audited zero-damage attack failure."""

import copy
import pytest
from packages.battle.runtime import Adapter, view as project
from packages.battle.agent import predict
from packages.simulation.resources import basic_energy, rain_damage
from ptcg.core.card_registry import registry
from packages.rules.effects import Dragapult
from ptcg.core.enums import CardPosition, PokemonPosition, CardType
from ptcg.utils.utils import current_player, opponent_player


def put(player, zone, cards):
    setattr(player, zone, cards)
    for i, card in enumerate(cards):
        card.cardPosition = CardPosition[zone.upper()]
        card.index = i + 1
        if zone in ("active", "bench"):
            card.position = PokemonPosition[zone.upper()]
            card.firstTurnPlayed = False


def scenario():
    game = Adapter(19)
    for _ in range(50):
        if game.env.phase == "playing":
            break
        game.submit(game.actor, predict(project(game, game.actor))[0])
    assert game.env.phase == "playing"
    state = game.env.gamestate
    player, opponent = current_player(state), opponent_player(state)
    player.firstTurn = opponent.firstTurn = False
    player.supporterPlayedTurn = True
    player.energyPlayedTurn = True
    attacker = registry.get("PAR-139")()
    attacker.abilityUsed = True
    attacker.energy = [CardType.METAL]
    attached = registry.get("SVE-008")()
    attached.cardPosition = CardPosition.ACTIVE_ATTACHMENT
    attacker.attachment = [attached]
    put(player, "active", [attacker])
    put(player, "bench", [])
    put(opponent, "active", [Dragapult()])
    put(opponent, "bench", [])
    put(
        player,
        "hand",
        [registry.get(x)() for x in ["PAR-163", "PAL-189", "PAL-189", "PAL-189"]],
    )
    put(player, "discard", [])
    put(player, "left", [registry.get("SVE-008")() for _ in range(8)])
    game.obs, _, game.done, game.info = game.env._prepare_step_result()
    return game, player, opponent


def test_resource_sequence_really_damages_opponent():
    game, player, opponent = scenario()
    actor = player.id
    hp = opponent.active[0].hp
    actions = []
    for _ in range(12):
        v = project(game, game.actor)
        command, _ = predict(v)
        if v["decision"]["kind"] == "options":
            option = next(
                o
                for o in v["decision"]["options"]
                if o["id"] == command["choice"]["optionId"]
            )
            actions.append((option["actionType"], option.get("source")))
        game.submit(game.actor, command)
        if game.env.gamestate.turn != actor:
            break
    assert actions[0] == ("UseItemAction", "Earthen Vessel")
    assert ("AttackAction", "Gholdengo ex") in actions
    assert hp - opponent.active[0].hp == 100
    assert sum(basic_energy(Adapter._card(c)) for c in player.hand) == 0


def test_selection_seeks_energy_instead_of_generic_ball():
    game, _, _ = scenario()
    v = project(game, game.actor)
    v["decision"] = {
        "id": "test",
        "kind": "selection",
        "min": 1,
        "max": 1,
        "zones": ["CardPosition.LEFT"],
        "hidden": False,
        "source": "Arven",
        "candidates": [
            {"ref": "ball", "card": Adapter._card(registry.get("PAF-084")())},
            {"ref": "energy-search", "card": Adapter._card(registry.get("PAR-163")())},
        ],
    }
    before = copy.deepcopy(v)
    assert predict(v)[0]["choice"] == {"selectedRefs": ["energy-search"]}
    assert v == before


def test_known_damage_only_zero_attack_is_not_preferred_but_utility_survives():
    game, _, _ = scenario()
    v = project(game, game.actor)
    attack = next(
        o for o in v["decision"]["options"] if o["actionType"] == "AttackAction"
    )
    v["decision"]["options"] = [attack, {"id": "pass", "actionType": "PassTurn"}]
    assert predict(v)[0]["choice"]["optionId"] == "pass"
    attack["attack"] = {"name": "Evolution", "damage": 0}
    assert predict(v)[0]["choice"]["optionId"] == attack["id"]


@pytest.mark.parametrize(
    "card_id,expected", [("PAL-189", False), ("SVE-008", True), ("P01-003", False)]
)
def test_energy_is_type_based(card_id, expected):
    card = Adapter._card(registry.get(card_id)())
    card["name"] = "Energy" if not expected else "基本钢能量"
    assert basic_energy(card) is expected


def test_rain_estimate_handles_weakness_and_resistance():
    assert rain_damage(0, {"cardType": "METAL"}, {"resistance": ["METAL"]}) == 0
    assert rain_damage(2, {"cardType": "METAL"}, {"weakness": ["METAL"]}) == 200
    assert rain_damage(2, {"cardType": "METAL"}, {"resistance": ["METAL"]}) == 70

from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType
from packages.rules.damage_events import deal
from packages.rules.knockouts import resolve_group
from tests.cardpool.test_checkup import board
from tests.cardpool.test_dual_and_locks import ability
from test_effects import drive


def test_knockout_search_is_defenders_private_choice_before_prizes():
    s, p, o = board()
    target = o.active[0]
    ability(target, "Gold Coffin", {"kind": "damage_choice", "knockout": True, "operation": "search_hand"})
    deal(p.active[0], target, target.hp, s)
    gen = resolve_group(s, damage_targets=[target])
    step = next(gen)
    actions = step[3]["raw_available_actions"]
    assert all(a.playerId == o.id for a in actions)
    assert target in o.active
    selected = actions[-1].chosen[0]
    try:
        step = gen.send(actions[-1])
        while True:
            step = gen.send(step[3]["raw_available_actions"][-1])
    except StopIteration:
        pass
    assert selected in o.hand and target in o.discard and len(p.prize) == 5


def test_team_knockout_recovers_physical_basic_energy_to_defender():
    s, p, o = board()
    target = o.active[0]
    target.cardType = CardType.WATER
    energy = registry.get("P4E-003")()
    target.attachment = [energy]
    ability(o.bench[0], "Diver's Catch", {"kind": "damage_choice", "knockout": True,
        "team": True, "targetType": "WATER", "optional": True, "operation": "recover_energy", "type": "WATER", "basic": True})
    deal(p.active[0], target, target.hp, s)
    drive(resolve_group(s, damage_targets=[target]))
    assert energy in o.hand and energy not in o.discard and target in o.discard


def test_damage_reaction_discards_attackers_energy_even_when_defender_survives():
    s, p, o = board()
    target = o.active[0]
    energy = registry.get("SVE-008")()
    p.active[0].attachment = [energy]
    ability(target, "Counterattacking Pincer", {"kind": "damage_choice", "activeOnly": True, "operation": "discard_attacker_energy"})
    deal(p.active[0], target, 10, s)
    drive(resolve_group(s))
    assert energy in p.discard and energy not in p.active[0].attachment
    assert len(p.prize) == 6

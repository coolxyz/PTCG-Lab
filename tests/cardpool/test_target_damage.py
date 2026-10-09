import pytest
from ptcg.core.card_registry import registry
from ptcg.core.enums import PokemonRule
from ptcg.utils.utils import next_turn, switch_pokemon
from packages.rules.attack_restrictions import allowed
from tests.cardpool.test_field_effects import field_fixture
from test_effects import zone, drive


def test_targeted_damage_applies_weakness_only_to_active_and_groups_knockouts():
    s, p, o, c, t, a = field_fixture(
        {"kind": "target_damage", "amount": 30, "count": 2, "zone": "all"}
    )
    b = registry.get("P01-005")()
    zone(o, "bench", [b])
    t.hp, b.hp = 50, 40
    t.weakness = b.weakness = [c.cardType]
    t.resistance = b.resistance = []
    drive(c.reduce_action(a, s))
    assert t in o.discard and o.active == [b] and b.hp == 10
    assert len(p.prize) == 5


@pytest.mark.parametrize("tera", [False, True])
def test_targeted_bench_damage_respects_tera(tera):
    s, p, o, c, t, a = field_fixture(
        {"kind": "target_damage", "amount": 30, "count": 1, "zone": "bench"}
    )
    b = registry.get("P01-005")()
    zone(o, "bench", [b])
    b.hp = 100
    if tera:
        b.pokemonRule = PokemonRule.TERA
    drive(c.reduce_action(a, s))
    assert b.hp == (100 if tera else 70) and s.turn == o.id


def test_targeted_empty_bench_still_pays_after_attack_discard():
    s, p, o, c, t, a = field_fixture(
        {
            "kind": "target_damage",
            "amount": 30,
            "count": 1,
            "zone": "bench",
            "discardEnergy": 2,
        }
    )
    zone(o, "bench", [])
    energies = [registry.get("SVE-008")() for _ in range(2)]
    c.attachment = energies[:]
    drive(c.reduce_action(a, s))
    assert not c.attachment and all(e in p.discard for e in energies) and s.turn == o.id


@pytest.mark.parametrize("duration", ["active", "next_turn"])
def test_named_lock_only_blocks_named_attack_and_switch_clears_it(duration):
    rule = {
        "kind": "named_attack_lock",
        "name": "Directed mechanism",
        "duration": duration,
    }
    s, p, o, c, t, a = field_fixture(rule)
    t.hp = 1000
    zone(p, "bench", [registry.get("P01-005")()])
    drive(c.reduce_action(a, s))
    next_turn(s)
    assert not allowed(c, c.attacks[0], s)
    other = registry.get("P01-005")().attacks[0]
    assert allowed(c, other, s)
    b = p.bench[0]
    switch_pokemon(c, b, p)
    switch_pokemon(b, c, p)
    assert allowed(c, c.attacks[0], s)


def test_first_turn_attack_requirement_uses_actual_players_first_turn():
    s, p, o, c, t, a = field_fixture(
        {
            "kind": "damage_expression",
            "term": "own_hand",
            "mode": "multiply",
            "factor": 10,
            "firstTurnForbidden": True,
        }
    )
    p.firstTurn = True
    assert not allowed(c, c.attacks[0], s)
    p.firstTurn = False
    assert allowed(c, c.attacks[0], s)

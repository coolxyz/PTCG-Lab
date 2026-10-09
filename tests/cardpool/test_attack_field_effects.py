import pytest
from ptcg.core.card_registry import registry
from ptcg.core.exceptions import GameTermination
from ptcg.core.reducer import _calculate_damage
from ptcg.utils.utils import switch_pokemon
from packages.rules.damage_events import deal, finish
from packages.rules.modifiers import attack_damage
from tests.cardpool.test_field_effects import field_fixture
from test_effects import zone, drive


def fixture(operation, **kw):
    return field_fixture({"kind": "extended_field", "operation": operation, **kw})


def test_return_stack_to_hand_promotes_without_awarding_prizes():
    s, p, o, c, t, a = fixture("return_self", destination="hand")
    b = registry.get("P01-005")()
    zone(p, "bench", [b])
    energy = registry.get("SVE-008")()
    c.attachment = [energy]
    drive(c.reduce_action(a, s))
    assert p.active == [b] and c in p.hand and energy in p.hand
    assert len(o.prize) == 6


def test_return_last_pokemon_loses_without_knockout():
    s, p, o, c, t, a = fixture("return_self", destination="hand")
    zone(p, "bench", [])
    with pytest.raises(GameTermination):
        drive(c.reduce_action(a, s))
    assert s.group_winner == o.id and c in p.hand and len(o.prize) == 6


def test_instant_knockout_does_not_trigger_damage_survival_or_extra_prizes():
    s, p, o, c, t, a = fixture("instant_ko", recoil=200)
    t.spec = {"abilities": [{"kind": "survive_damage", "remainingHP": 10}]}
    t.hp = type(t)().hp
    c.hp = 300
    zone(o, "bench", [registry.get("P01-005")()])
    drive(c.reduce_action(a, s))
    assert t in o.discard and c.hp == 100 and len(p.prize) == 5


def test_instant_knockout_protection_also_prevents_conditional_recoil():
    s, p, o, c, t, a = fixture("instant_ko", recoil=200)
    t.attack_protection = {"effects": True, "turn": s.turn_number}
    hp = c.hp
    drive(c.reduce_action(a, s))
    assert t in o.active and c.hp == hp


def test_damage_reduction_before_weakness_and_removed_by_switch():
    s, p, o, c, t, a = fixture("reduce_damage", amount=30)
    drive(c.reduce_action(a, s))
    assert attack_damage(t, c, 100, s) == 70
    c.weakness, c.resistance = [t.cardType], []
    assert _calculate_damage(t, c, 100, s) == 140
    zone(o, "bench", [registry.get("P01-005")()])
    switch_pokemon(t, o.bench[0], o)
    assert not hasattr(t, "attack_damage_reduction")


def test_temporary_retaliation_works_without_ability_and_expires():
    s, p, o, c, t, a = fixture("retaliate", counters=60)
    drive(c.reduce_action(a, s))
    hp = t.hp
    deal(t, c, 10, s)
    finish(s)
    assert t.hp == hp - 60
    s.turn_number += 1
    deal(t, c, 10, s)
    finish(s)
    assert t.hp == hp - 60


def test_both_switch_opponent_controls_own_replacement():
    s, p, o, c, t, a = fixture("double_switch")
    own, other = registry.get("P01-005")(), registry.get("P01-006")()
    zone(p, "bench", [own])
    zone(o, "bench", [other])
    seen = drive(c.reduce_action(a, s))
    assert [x["raw_available_actions"][0].playerId for x in seen] == [p.id, o.id]
    assert p.active == [own] and o.active == [other]

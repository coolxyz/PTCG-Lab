import random
from types import SimpleNamespace
import pytest
from ptcg.core.card_registry import registry
from ptcg.core.enums import Stage, PokemonType
from ptcg.core.reducer import _calculate_damage, reduce_attack_damage
from ptcg.utils.utils import next_turn, switch_pokemon
from packages.rules.protection import blocked
from packages.rules.field_effects import resolve as field_effect
from packages.rules.zone_effects import resolve as zone_effect
from tests.cardpool.test_field_effects import field_fixture
from tests.cardpool.test_shared_abilities import board
from test_effects import drive, zone


@pytest.mark.parametrize("heads", [True, False])
def test_protection_coin_and_duration(heads):
    s, p, o, c, t, a = field_fixture(
        {"kind": "attack_protection", "damage": True, "effects": True, "coin": True}
    )
    s.rng = random.Random(1 if heads else 0)
    drive(c.reduce_action(a, s))
    assert s.turn == o.id
    assert blocked(c, s, "damage") == heads and blocked(c, s, "effects") == heads
    next_turn(s)
    assert not hasattr(c, "attack_protection")


def test_protection_leaving_active_clears_it():
    s, p, o, c, t, a = field_fixture({"kind": "attack_protection", "damage": True})
    b = registry.get("P01-005")()
    zone(p, "bench", [b])
    drive(c.reduce_action(a, s))
    switch_pokemon(c, b, p)
    switch_pokemon(b, c, p)
    assert not blocked(c, s, "damage")


@pytest.mark.parametrize("kind", ["damage", "effects"])
def test_damage_and_effect_protection_have_separate_boundaries(kind):
    s, p, o, c, t, a = field_fixture(
        {"kind": "place_counters", "zone": "all", "select": False, "amount": 20}
    )
    t.hp = 300
    t.weakness = t.resistance = []
    t.attack_protection = {"turn": s.turn_number, kind: True}
    assert _calculate_damage(c, t, 60, s) == (0 if kind == "damage" else 60)
    drive(field_effect(c.spec["attacks"][0]["mechanic"], a, s))
    assert t.hp == (280 if kind == "damage" else 300)
    energy = registry.get("SVE-008")()
    t.attachment = [energy]
    drive(zone_effect({"kind": "discard_opponent_energy", "count": 1}, a, s))
    assert (energy in t.attachment) == (kind == "effects")


def test_protection_does_not_block_opponent_hand_disruption():
    s, p, o, c, t, a = field_fixture({"kind": "random_discard_hand"})
    t.attack_protection = {"turn": s.turn_number, "effects": True}
    before = len(o.hand)
    drive(zone_effect({"kind": "random_discard_hand"}, a, s))
    assert len(o.hand) == before - 1


def test_ignore_effects_attack_bypasses_damage_protection():
    s, p, o, c, t, a = field_fixture(
        {"kind": "ignore_damage_modifiers", "ignore": "effects"}
    )
    t.attack_protection = {"turn": s.turn_number, "damage": True}
    t.hp = 300
    t.weakness = t.resistance = []
    a.attack.damage = 80
    drive(reduce_attack_damage(a, s, ignore_effects=True))
    assert t.hp == 220


def test_source_condition_uses_physical_attacker_and_only_protects_from_basic():
    s, p, o, c, t, a = field_fixture({"kind": "attack_protection"})
    t.attack_protection = {"turn": s.turn_number, "damage": True, "source": "basic"}
    c.stage = Stage.BASIC
    assert blocked(t, s, "damage", c)
    c.stage = Stage.STAGE_1
    assert not blocked(t, s, "damage", c)


def test_passive_protection_respects_ex_source_and_ability_suppression():
    s, p, o, c = board(
        {"kind": "protection", "trigger": "passive", "damage": True, "source": "ex_v"}
    )
    attacker = o.active[0]
    attacker.pokemonType = PokemonType.EX
    assert _calculate_damage(attacker, c, 50, s) == 0
    attacker.pokemonType = PokemonType.NORMAL
    assert _calculate_damage(attacker, c, 50, s) > 0
    attacker.pokemonType = PokemonType.EX
    attacker.ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    assert _calculate_damage(attacker, c, 50, s) > 0


def test_bench_passive_protection_does_not_apply_while_active():
    s, p, o, c = board(
        {
            "kind": "protection",
            "trigger": "passive",
            "damage": True,
            "effects": True,
            "zone": "bench",
        }
    )
    attacker = o.active[0]
    assert not blocked(c, s, "effects", attacker)
    switch_pokemon(c, p.bench[0], p)
    assert (
        blocked(c, s, "effects", attacker)
        and _calculate_damage(attacker, c, 50, s) == 0
    )


def test_status_is_prevented_but_self_effect_remains():
    r = {
        "kind": "special_status",
        "status": "POISONED",
        "target": "opponent",
        "coin": False,
    }
    s, p, o, c, t, a = field_fixture(r)
    t.attack_protection = {"turn": s.turn_number, "effects": True}
    drive(c.resolve_mechanic(r, a, s))
    assert not getattr(t, "poisoned", False)
    drive(c.resolve_mechanic({**r, "target": "self"}, a, s))
    assert c.poisoned

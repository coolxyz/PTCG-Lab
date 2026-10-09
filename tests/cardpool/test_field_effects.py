import pytest
from packages.rules.field_effects import resolve
from ptcg.core.card_registry import registry
from ptcg.core.enums import PokemonRule, SpecialCondition
from test_effects import zone, drive
from tests.cardpool.test_attack_expressions import fixture
from copy import deepcopy
from packages.rules.plain import PlainPokemon
from ptcg.core.action import AttackAction


def field_fixture(mechanic):
    s, p, o, original, target, _ = fixture("bench_damage")
    spec = deepcopy(original.spec)
    spec["attacks"] = [
        {"name": "Directed mechanism", "damage": 0, "cost": [], "mechanic": mechanic}
    ]
    source = type("DirectedPokemon", (PlainPokemon,), {"spec": spec})()
    zone(p, "active", [source])
    return (
        s,
        p,
        o,
        source,
        target,
        AttackAction(p.id, source, source.attacks[0], target),
    )


def test_bench_attack_resolves_all_knockouts_before_prizes_and_promotion():
    s, p, o, source, target, action = fixture("bench_damage")
    bench = registry.get("P01-005")()
    survivor = registry.get("P01-006")()
    zone(o, "bench", [survivor, bench])
    target.hp = bench.hp = 1
    # drive chooses the last available target.
    drive(source.reduce_action(action, s))
    assert target in o.discard and bench in o.discard
    assert len(p.prize) == 4 and o.active == [survivor]


def test_bench_damage_does_not_apply_weakness_and_tera_blocks_it():
    s, p, o, source, _, action = fixture("bench_damage")
    bench = registry.get("P01-005")()
    zone(o, "bench", [bench])
    bench.hp = 200
    bench.weakness = [source.cardType]
    drive(resolve({"kind": "bench_damage", "amount": 30}, action, s))
    assert bench.hp == 170
    bench.pokemonRule = PokemonRule.TERA
    drive(resolve({"kind": "bench_damage", "amount": 30}, action, s))
    assert bench.hp == 170


@pytest.mark.parametrize("target", ["one", "all"])
def test_heal_own_field_caps_at_maximum(target):
    s, p, o, source, opponent, action = fixture("heal_field")
    bench = registry.get("P01-005")()
    zone(p, "bench", [bench])
    source.hp -= 10
    bench.hp -= 30
    old_opponent = opponent.hp
    drive(resolve({"kind": "heal_field", "target": target, "amount": 20}, action, s))
    assert source.hp == type(source)().hp - (0 if target == "all" else 10)
    assert bench.hp == type(bench)().hp - 10
    assert opponent.hp == old_opponent


def test_mill_and_recovery_touch_only_the_specified_objects():
    s, p, o, source, target, action = fixture("mill_self")
    top = list(p.left[:2])
    other = list(o.left)
    drive(resolve({"kind": "mill_self", "count": 2}, action, s))
    assert all(c in p.discard and c not in p.left for c in top)
    assert o.left == other
    source.poisoned = source.burned = target.poisoned = True
    source.special_condition = SpecialCondition.CONFUSED
    drive(resolve({"kind": "recover_status"}, action, s))
    assert not any(
        hasattr(source, a) for a in ("poisoned", "burned", "special_condition")
    )
    assert target.poisoned


def test_gust_attacker_chooses_and_clears_old_active_conditions():
    s, p, o, _, target, action = fixture("gust")
    bench = registry.get("P01-005")()
    zone(o, "bench", [bench])
    target.poisoned = True
    drive(resolve({"kind": "gust", "coin": False}, action, s))
    assert o.active == [bench] and target in o.bench
    assert not hasattr(target, "poisoned")


def test_counters_bypass_tera_and_do_not_trigger_damage_knockout_tools():
    s, p, o, source, _, action = field_fixture(
        {"kind": "place_counters", "zone": "bench", "select": True, "amount": 20}
    )
    bench = registry.get("P01-005")()
    zone(o, "bench", [bench])
    bench.pokemonRule = PokemonRule.TERA
    bench.hp = 100
    drive(
        resolve(
            {"kind": "place_counters", "zone": "bench", "select": True, "amount": 20},
            action,
            s,
        )
    )
    assert bench.hp == 80 and bench not in action.group_damage_targets


def test_spread_damage_applies_weakness_only_to_active():
    s, p, o, source, target, action = field_fixture(
        {"kind": "spread_damage", "zone": "all", "amount": 30}
    )
    bench = registry.get("P01-005")()
    zone(o, "bench", [bench])
    target.hp = bench.hp = 200
    target.weakness = bench.weakness = [source.cardType]
    target.resistance = bench.resistance = []
    drive(resolve({"kind": "spread_damage", "zone": "all", "amount": 30}, action, s))
    assert target.hp == 140 and bench.hp == 170


def test_zero_damage_counter_attack_still_awards_knockout_prize():
    s, p, o, source, target, action = field_fixture(
        {"kind": "place_counters", "zone": "bench", "select": True, "amount": 20}
    )
    # Set every possible selected target to lethal HP.
    bench = registry.get("P01-005")()
    zone(o, "bench", [bench])
    bench.hp = 1
    drive(source.reduce_action(action, s))
    assert bench in o.discard and len(p.prize) == 5

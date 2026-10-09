import random
import pytest
from packages.rules.attack_math import damage
from packages.rules.adapter import Adapter
from packages.rules.plain import SPECS
from ptcg.core.action import AttackAction, RetreatAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import SpecialCondition, CardType
from ptcg.utils.utils import next_turn, switch_pokemon
from scripts.cardpool.attack_rules import compile_expression
from test_effects import context, zone, drive


def fixture(kind):
    spec = next(
        s
        for s in SPECS
        if any(a.get("mechanic", {}).get("kind") == kind for a in s["attacks"])
    )
    state, p, o = context()
    source = registry.get(spec["effectKey"])()
    zone(p, "active", [source])
    target = o.active[0]
    target.weakness, target.resistance = [], []
    attack = next(
        a
        for a, d in zip(source.attacks, spec["attacks"])
        if d.get("mechanic", {}).get("kind") == kind
    )
    source.energy = attack.cost[:]
    return state, p, o, source, target, AttackAction(p.id, source, attack, target)


@pytest.mark.parametrize(
    "term,count",
    [
        ("self_counters", 2),
        ("opponent_counters", 3),
        ("self_energy", 2),
        ("opponent_energy", 3),
        ("active_energy", 5),
        ("own_bench", 0),
        ("opponent_bench", 2),
        ("opponent_prizes_taken", 2),
        ("own_prizes_taken", 1),
        ("self_damaged", 1),
        ("opponent_damaged", 1),
        ("opponent_special", 1),
    ],
)
@pytest.mark.parametrize("mode", ["add", "multiply", "subtract"])
def test_damage_terms_and_modes_have_independent_expected_values(term, count, mode):
    s, p, o, source, target, action = fixture("damage_expression")
    source.hp = type(source)().hp - 20
    target.hp = type(target)().hp - 30
    source.energy = [CardType.ANY] * 2
    target.energy = [CardType.METAL] * 3
    target.special_condition = SpecialCondition.CONFUSED
    zone(o, "bench", [registry.get("P01-005")(), registry.get("P01-006")()])
    p.prize, o.prize = p.prize[:5], o.prize[:4]
    action.attack.damage = 20
    result = damage({"term": term, "mode": mode, "factor": 30}, action, s)
    assert result == (
        count * 30
        if mode == "multiply"
        else max(0, 20 + count * (30 if mode == "add" else -30))
    )


@pytest.mark.parametrize("clear", ["turn", "switch"])
def test_attack_lock_blocks_exactly_next_own_turn_and_switch_clears(clear):
    s, p, o, source, target, action = fixture("attack_lock")
    target.hp = 1000
    zone(p, "bench", [registry.get("P01-005")()])
    drive(source.reduce_action(action, s))
    assert s.turn == o.id and Adapter._card(source)["attackBlocked"]
    next_turn(s)
    assert s.turn == p.id and not source.get_actions(s)
    assert not any(isinstance(a, AttackAction) for a in p.get_actions(s))
    if clear == "switch":
        bench = p.bench[0]
        switch_pokemon(source, bench, p)
        switch_pokemon(bench, source, p)
    else:
        next_turn(s)
        next_turn(s)
    assert source.get_actions(s)
    assert "attackBlocked" not in Adapter._card(source)


@pytest.mark.parametrize(
    "status", [SpecialCondition.ASLEEP, SpecialCondition.PARALYZED]
)
def test_sleep_paralysis_block_attack_and_retreat_but_not_pass(status):
    s, p, _, source, _, _ = fixture("special_status")
    source.special_condition = status
    source.retreat = []
    zone(p, "bench", [registry.get("P01-005")()])
    actions = p.get_actions(s)
    assert not any(isinstance(a, (AttackAction, RetreatAction)) for a in actions)
    assert any(type(a).__name__ == "PassTurn" for a in actions)


@pytest.mark.parametrize("heads", [True, False])
def test_sleep_is_checked_for_both_players_between_turns(heads):
    s, p, o, source, target, _ = fixture("special_status")
    source.special_condition = target.special_condition = SpecialCondition.ASLEEP
    # Seed 1 has two heads; 0 has two tails.
    s.rng = random.Random(1 if heads else 0)
    next_turn(s)
    assert hasattr(source, "special_condition") != heads
    assert hasattr(target, "special_condition") != heads
    assert s.turn == o.id


def test_paralysis_expires_only_after_affected_players_turn():
    s, p, o, _, target, _ = fixture("special_status")
    target.special_condition = SpecialCondition.PARALYZED
    next_turn(s)
    assert target.special_condition == SpecialCondition.PARALYZED
    next_turn(s)
    assert not hasattr(target, "special_condition") and s.turn == p.id


@pytest.mark.parametrize(
    "ignore,expected", [("resistance", 80), ("effects", 70), ("all", 100)]
)
def test_attack_modifier_ignores_do_not_ignore_unrelated_layers(ignore, expected):
    from ptcg.core.reducer import reduce_attack_damage

    s, p, o, source, target, action = fixture("ignore_damage_modifiers")
    action.attack.damage = 100
    target.hp = 500
    target.weakness, target.resistance = [], [source.cardType]
    target.damage_shield = {"turn": s.turn_number, "amount": 20}
    drive(
        reduce_attack_damage(
            action,
            s,
            apply_weakness_resistance=ignore != "all",
            ignore_effects=ignore in ("effects", "all"),
            ignore_resistance=ignore == "resistance",
        )
    )
    assert target.hp == 500 - expected


def test_tera_bench_prevents_attack_damage_but_not_damage_counters():
    from ptcg.core.reducer import reduce_attack_damage
    from ptcg.core.enums import PokemonRule

    s, p, o, source, target, action = fixture("ignore_damage_modifiers")
    zone(o, "active", [registry.get("P01-005")()])
    zone(o, "bench", [target])
    target.pokemonRule, target.hp = PokemonRule.TERA, 100
    action.attack.damage = 60
    drive(reduce_attack_damage(action, s, apply_weakness_resistance=False))
    assert target.hp == 100
    target.hp -= 20
    assert target.hp == 80


def test_expression_requires_matching_numbers_and_complete_rules():
    rule = {
        "damage": "20",
        "damageP": "加",
        "eeffect": "This attack does 30 more damage for each Energy attached to this Pokémon.",
        "effectZHS": "追加造成这只宝可梦身上附着的能量数量×30伤害。",
    }
    assert compile_expression(rule)["term"] == "self_energy"
    assert (
        compile_expression({**rule, "effectZHS": rule["effectZHS"].replace("30", "40")})
        is None
    )
    assert (
        compile_expression({**rule, "eeffect": rule["eeffect"] + " Draw a card."})
        is None
    )

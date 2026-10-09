import random
import pytest
from ptcg.core.card_registry import registry
from ptcg.core.enums import SpecialCondition
from ptcg.core.exceptions import GameTermination
from ptcg.utils.utils import next_turn, switch_pokemon, judge_termination
from packages.rules.checkup import finish
from packages.rules.knockouts import resolve_group
from test_effects import context, zone, drive


def board():
    s, p, o = context()
    for player in (p, o):
        zone(player, "active", [registry.get("P01-005")()])
        zone(player, "bench", [registry.get("P01-006")()])
        zone(player, "prize", [registry.get("SVE-008")() for _ in range(6)])
        zone(player, "left", [registry.get("SVE-008")() for _ in range(8)])
        player.hasPokemonDead = False
    return s, p, o


def test_enhanced_poison_ticks_and_switch_resets_amount():
    s, p, o = board()
    target = o.active[0]
    target.poisoned = True
    target.poison_damage = 30
    before = target.hp
    next_turn(s)
    drive(finish(s))
    assert target.hp == before - 30
    switch_pokemon(target, o.bench[0], o)
    assert not hasattr(target, "poison_damage") and not getattr(
        target, "poisoned", False
    )


@pytest.mark.parametrize("heads", [True, False])
def test_poison_and_burn_stack_and_burn_flips_after_damage(heads):
    s, p, o = board()
    target = o.active[0]
    target.poisoned = target.burned = True
    target.hp = 70
    s.rng = random.Random(1 if heads else 0)
    next_turn(s)
    assert s.turn == p.id and len(o.left) == 8
    drive(finish(s))
    assert target.hp == 40 and target.poisoned
    assert bool(getattr(target, "burned", False)) != heads
    assert s.turn == o.id and len(o.left) == 7


def test_condition_knockout_is_processed_before_next_draw_and_not_attack_knockout():
    s, p, o = board()
    target = o.active[0]
    target.hp, target.poisoned = 10, True
    holder = o.bench[0]
    holder.attachment = [registry.get("P01-002")()]
    energy = registry.get("SVE-008")()
    target.attachment = [energy]
    next_turn(s)
    gen = finish(s)
    item = next(gen)
    assert len(o.left) == 8 and s.turn == p.id
    assert energy in o.discard and energy not in holder.attachment
    try:
        while True:
            item = gen.send(list(item[3]["raw_available_actions"])[-1])
    except StopIteration:
        pass
    assert s.turn == o.id and len(o.left) == 7
    assert len(p.prize) == 5 and not o.hasPokemonDead


@pytest.mark.parametrize("poison", [True, False])
def test_switch_clears_all_conditions(poison):
    s, p, _ = board()
    source = p.active[0]
    source.poisoned, source.burned = poison, True
    source.special_condition = SpecialCondition.ASLEEP
    switch_pokemon(source, p.bench[0], p)
    assert not any(
        hasattr(source, x) for x in ("poisoned", "burned", "special_condition")
    )


def test_simultaneous_knockouts_take_both_prizes_before_promotions():
    s, p, o = board()
    a, b = p.active[0], o.active[0]
    a.hp = b.hp = 0
    drive(resolve_group(s, damage_targets=[b]))
    assert a in p.discard and b in o.discard
    assert len(p.prize) == len(o.prize) == 5
    assert p.active and o.active
    assert not judge_termination(s)[0]


@pytest.mark.parametrize(
    "p_bench,o_bench,p_prizes,o_prizes,winner",
    [
        (True, True, 1, 1, "tie"),
        (True, False, 1, 1, "p"),
        (False, True, 1, 1, "o"),
        (False, False, 6, 6, "tie"),
    ],
)
def test_simultaneous_victory_uses_all_win_conditions(
    p_bench, o_bench, p_prizes, o_prizes, winner
):
    s, p, o = board()
    if not p_bench:
        p.bench = []
    if not o_bench:
        o.bench = []
    p.prize, o.prize = p.prize[:p_prizes], o.prize[:o_prizes]
    p.active[0].hp = o.active[0].hp = 0
    with pytest.raises(GameTermination):
        drive(resolve_group(s))
    done, actual = judge_termination(s)
    assert done and actual == ({"p": p.id, "o": o.id, "tie": None}[winner])
    assert len(p.prize) == p_prizes - 1 and len(o.prize) == o_prizes - 1

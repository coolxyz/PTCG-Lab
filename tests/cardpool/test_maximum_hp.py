"""Maximum HP changes preserve damage and settle knockouts before turn advance."""

import pytest
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, Stage
from packages.rules.maximum_hp import maximum, reconcile
from packages.rules.knockouts import resolve_group
from tests.cardpool.test_tool_modifiers import board, attach
from tests.cardpool.test_shared_abilities import board as ability_board
from test_effects import zone, drive


def test_charm_attachment_and_removal_preserve_damage():
    s, p, o, a, b = board()
    printed = a.hp
    a.hp -= 20
    tool = attach("勇氣護符", s, p, a)
    reconcile(s)
    assert maximum(a) == printed + 50 and a.hp == printed + 30
    reconcile(s)
    assert a.hp == printed + 30
    a.attachment.remove(tool)
    reconcile(s)
    assert maximum(a) == printed and a.hp == printed - 20


def test_charm_stops_applying_to_evolved_holder():
    s, p, o, a, b = board()
    printed = a.hp
    attach("勇氣護符", s, p, a)
    reconcile(s)
    a.stage = Stage.STAGE_1
    reconcile(s)
    assert maximum(a) == printed and a.hp == printed


def test_energy_hp_condition_counts_units_and_reverts_without_healing():
    s, p, o, a = ability_board(
        {
            "kind": "continuous",
            "trigger": "passive",
            "scope": "self",
            "hp": 100,
            "energy": "METAL",
            "energyMinimum": 3,
        }
    )
    printed = a.hp
    a.energy = [CardType.METAL, CardType.ANY]
    reconcile(s)
    assert maximum(a) == printed
    a.energy.append(CardType.ANY)
    reconcile(s)
    assert maximum(a) == printed + 100
    a.hp -= 40
    a.energy.pop()
    reconcile(s)
    assert a.hp == printed - 40


def test_hp_aura_does_not_stack_and_loss_can_knock_out_bench():
    s, p, o, a = ability_board(
        {
            "kind": "continuous",
            "trigger": "passive",
            "hp": 40,
            "noStack": "Vibrant Dance",
        }
    )
    spare = type(a)()
    fragile = registry.get("P01-005")()
    printed = fragile.hp
    zone(p, "bench", [spare, fragile])
    reconcile(s)
    assert maximum(fragile) == printed + 40
    fragile.hp = 20
    p.bench.remove(spare)
    reconcile(s)
    assert fragile.hp == 20
    # The provider is knocked out; removing its aura causes a second knockout.
    a.hp = 0
    survivor = registry.get("P01-005")()
    zone(p, "bench", p.bench + [survivor])
    prizes = len(o.prize)
    reward = a.prize + fragile.prize
    drive(resolve_group(s))
    assert fragile in p.discard and a in p.discard
    assert len(o.prize) == prizes - reward
    assert p.active == [survivor]


@pytest.mark.parametrize(
    "title,prefix,bonus",
    [
        ("英雄斗篷", "", 100),
        ("竹蘭的力量負重", "Cynthia's ", 70),
    ],
)
def test_tool_hp_is_reversible(title, prefix, bonus):
    s, p, o, a, b = board()
    printed = a.hp
    if prefix:
        a.name = prefix + a.name
    tool = attach(title, s, p, a)
    reconcile(s)
    assert maximum(a) == printed + bonus
    a.attachment.remove(tool)
    reconcile(s)
    assert maximum(a) == printed and a.hp == printed


def test_evolution_keeps_damage_when_basic_only_hp_bonus_ends():
    from packages.rules.engine import Gholdengo
    from ptcg.core.action import EvolvePokemonAction
    from ptcg.core.reducer import reduce_evolve_pokemon_action
    from packages.rules.attack_math import counters

    s, p, o, a, b = board()
    attach("勇氣護符", s, p, a)
    a.hp -= 40
    evolved = Gholdengo()
    zone(p, "hand", [evolved])
    reduce_evolve_pokemon_action(EvolvePokemonAction(p.id, evolved, a), s)
    reconcile(s)
    assert evolved.hp == type(evolved)().hp - 40
    assert counters(evolved) == 4


def test_heal_uses_increased_maximum_not_printed_hp():
    from types import SimpleNamespace
    from packages.rules.zone_effects import resolve

    s, p, o, a, b = board()
    printed = a.hp
    attach("英雄斗篷", s, p, a)
    a.hp -= 20
    drive(resolve({"kind": "heal_self", "amount": 30}, SimpleNamespace(source=a), s))
    assert a.hp == printed + 100


def test_hp_removal_ends_match_before_next_player_draw():
    from ptcg.utils.utils import next_turn
    from ptcg.core.exceptions import GameTermination
    from packages.rules.maximum_hp import settle

    s, p, o, a, b = board()
    tool = attach("英雄斗篷", s, p, a)
    a.hp = 10
    a.attachment.remove(tool)
    o.bench = []
    before = (s.turn_number, len(o.left))
    next_turn(s)
    assert s.pending_hp_turn
    with pytest.raises(GameTermination):
        drive(settle(s))
    assert before == (s.turn_number, len(o.left))

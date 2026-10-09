import pytest
from packages.rules.plain import SPECS
from packages.rules.adapter import Adapter
from ptcg.core.card_registry import registry
from ptcg.core.action import AttackAction, EvolvePokemonAction, EffectAction
from ptcg.core.reducer import (
    _calculate_damage,
    reduce_attack_damage,
    reduce_effect_action,
)
from ptcg.utils.utils import next_turn, switch_pokemon
from test_effects import context, zone, drive


def setup():
    spec = next(
        s
        for s in SPECS
        if any(
            a.get("mechanic", {}).get("kind") == "damage_shield" and a["damage"] == 0
            for a in s["attacks"]
        )
    )
    state, player, opponent = context()
    defender = registry.get(spec["effectKey"])()
    zone(player, "active", [defender])
    index = next(
        i
        for i, a in enumerate(spec["attacks"])
        if a.get("mechanic", {}).get("kind") == "damage_shield" and a["damage"] == 0
    )
    attack = defender.attacks[index]
    amount = spec["attacks"][index]["mechanic"]["amount"]
    drive(
        defender.reduce_action(
            AttackAction(player.id, defender, attack, opponent.active[0]), state
        )
    )
    assert state.turn == opponent.id
    return state, player, opponent, defender, amount


@pytest.mark.parametrize("modifier", ["neutral", "weakness", "resistance"])
def test_damage_shield_applies_after_type_modifiers_and_clamps_at_zero(modifier):
    state, player, opponent, defender, amount = setup()
    source = opponent.active[0]
    defender.weakness = [source.cardType] if modifier == "weakness" else []
    defender.resistance = [source.cardType] if modifier == "resistance" else []
    before = 100 if modifier == "weakness" else 20 if modifier == "resistance" else 50
    assert _calculate_damage(source, defender, 50, state) == max(0, before - amount)
    assert _calculate_damage(source, defender, 0, state) == 0
    assert Adapter._card(defender)["attackDamageReduction"] == amount


@pytest.mark.parametrize("clear", ["turn_end", "switch", "evolve"])
def test_damage_shield_expires_or_clears_on_switch_and_evolution(clear):
    state, player, opponent, defender, amount = setup()
    if clear == "turn_end":
        next_turn(state)
    elif clear == "switch":
        other = registry.get("P01-006")()
        zone(player, "bench", [other])
        switch_pokemon(defender, other, player)
        switch_pokemon(other, defender, player)
    else:
        next_turn(state)
        # Restore a marker to independently test evolution, not expiry.
        defender.damage_shield = {"turn": state.turn_number, "amount": amount}
        spec = next(s for s in SPECS if s["stage"] == "STAGE_1")
        evolution = registry.get(spec["effectKey"])()
        zone(player, "hand", [evolution])
        drive(
            evolution.reduce_action(
                EvolvePokemonAction(player.id, evolution, defender), state
            )
        )
        defender = evolution
    assert "attackDamageReduction" not in Adapter._card(defender)


def test_damage_shield_applies_to_attack_without_weakness_but_not_damage_counters():
    from ptcg.core.effect import Effect

    state, player, opponent, defender, amount = setup()
    source = opponent.active[0]
    defender.hp = 1000
    action = AttackAction(opponent.id, source, source.attacks[0], defender)
    action.attack.damage = 50
    drive(reduce_attack_damage(action, state, apply_weakness_resistance=False))
    assert defender.hp == 1000 - max(0, 50 - amount)
    before = defender.hp
    effect = Effect(2)
    drive(
        reduce_effect_action(EffectAction(opponent.id, source, effect, defender), state)
    )
    assert defender.hp == before - 20


def test_mew_copied_shield_protects_mew_not_the_opponents_definition():
    from packages.rules.effects import Mew

    state, player, opponent, defender, amount = setup()
    mew = Mew()
    zone(opponent, "active", [mew])
    attack = next(
        a
        for i, a in enumerate(defender.attacks)
        if defender.spec["attacks"][i].get("mechanic", {}).get("kind")
        == "damage_shield"
    )
    drive(
        mew._genome_hacking_attack(
            AttackAction(opponent.id, mew, mew.attacks[0], defender), state
        ),
        lambda actions, info, n: next(a for a in actions if a.chosen == [attack]),
    )
    assert mew.damage_shield == {"turn": state.turn_number, "amount": amount}
    assert not hasattr(defender, "damage_shield")

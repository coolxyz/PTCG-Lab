from types import SimpleNamespace
from ptcg.core.card_registry import registry
from ptcg.core.action import AttackAction
from ptcg.core.enums import PokemonRule, CardType, CardPosition
from packages.rules.advanced_attacks import resolve, field
from packages.rules.plain import SPECS
from tests.cardpool.test_checkup import board
from test_effects import drive, zone


def action(p, o):
    return AttackAction(p.id, p.active[0], p.active[0].attacks[0], o.active[0])


def test_repeat_target_applies_armor_once_to_aggregated_damage():
    s, p, o = board()
    target = o.active[0]
    target.hp = 500
    target.weakness = [p.active[0].cardType]
    target.damage_shield = {"turn": s.turn_number, "amount": 30}
    drive(resolve({"op": "repeat_target", "count": 6, "amount": 20}, action(p, o), s), lambda actions, info, n: actions.action_for_candidate_indices([0]))
    assert target.hp == 410


def test_return_energy_respects_protection_and_preserves_physical_cards():
    s, p, o = board()
    target = o.active[0]
    energy = registry.get("SVE-008")()
    target.attachment = [energy]
    target.dynamic_energy = True
    rule = {"operation": "return_enemy_energy", "count": 2}
    target.attack_protection = {"turn": s.turn_number, "effects": True}
    drive(field(rule, action(p, o), s))
    assert energy in target.attachment
    del target.attack_protection
    drive(field(rule, action(p, o), s))
    assert energy in o.left and not target.attachment and not target.energy


def test_discard_counters_shuffle_even_when_recipient_protected():
    s, p, o = board()
    energy = registry.get("P4E-001")()
    zone(p, "discard", [energy])
    target = o.active[0]
    target.attack_protection = {"turn": s.turn_number, "effects": True}
    hp = target.hp
    drive(field({"operation": "discard_energy_counters", "type": "GRASS", "amount": 20}, action(p, o), s))
    assert target.hp == hp and energy in p.left and not p.discard


def test_window_damage_counts_future_trainers_and_shuffles_only_remaining_cards():
    s, p, o = board()
    cards = [registry.get("SVE-008")() for _ in range(6)]
    cards[0].pokemonRule = PokemonRule.FUTURE
    cards[2].pokemonRule = PokemonRule.FUTURE
    original = cards[:]
    selected = {cards[0], cards[2]}
    zone(p, "left", cards)
    target = o.active[0]
    target.hp, target.weakness, target.resistance = 500, [], []
    drive(resolve({"op": "window_damage", "count": 5, "filter": "future", "factor": 70}, action(p, o), s))
    assert target.hp == 360
    assert set(p.discard) == selected
    assert set(p.left) == set(original) - selected


def test_copy_uses_physical_attacker_and_ignores_copied_named_lock():
    from packages.rules.copy_attacks import resolve as copy_attack
    s, p, o = board()
    spec = next(c for c in SPECS if c["attacks"] and len(c["attacks"]) == 1 and not c["attacks"][0].get("mechanic") and c["attacks"][0]["damage"] == 30)
    donor = registry.get(spec["effectKey"])()
    zone(o, "active", [donor])
    donor.hp, donor.weakness, donor.resistance = 500, [], []
    source = p.active[0]
    source.attack_locks = {donor.attacks[0].name: s.turn_number}
    before = s.turn_number
    drive(copy_attack({"kind": "copy_attack"}, action(p, o), s))
    assert donor.hp == 470
    assert s.turn_number == before + 1
    assert source.resolving_attack_name == source.attacks[0].name

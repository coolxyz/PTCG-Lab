from ptcg.core.action import AttackAction, EvolvePokemonAction
from ptcg.core.card_registry import registry
from ptcg.core.reducer import reduce_evolve_pokemon_action
from ptcg.utils.utils import switch_pokemon
from packages.rules.evolution_effects import devolve
from packages.rules.field_events import finish
from packages.rules.remaining_attacks import resolve
from tests.cardpool.test_checkup import board
from tests.cardpool.test_dual_and_locks import ability, klefki
from test_effects import zone, drive


def test_switch_trigger_is_captured_before_later_suppression_and_once_per_turn():
    s, p, o = board()
    c = p.bench[0]
    ability(c, "Tachyon Bits", {"kind": "move_active", "effect": {
        "kind": "counters", "zone": "any", "count": 1, "amount": 20}})
    switch_pokemon(p.active[0], c, p)
    klefki(o.bench[0])
    switch_pokemon(o.active[0], o.bench[0], o)
    before = sum(t.hp for t in o.active + o.bench)
    drive(finish(s))
    assert sum(t.hp for t in o.active + o.bench) == before - 20
    switch_pokemon(c, p.bench[0], p)
    switch_pokemon(p.active[0], c, p)
    drive(finish(s))
    assert sum(t.hp for t in o.active + o.bench) == before - 20


def test_suppression_at_switch_prevents_trigger():
    s, p, o = board()
    klefki(o.active[0])
    c = p.bench[0]
    ability(c, "Tachyon Bits", {"kind": "move_active", "effect": {
        "kind": "counters", "zone": "any", "count": 1, "amount": 20}})
    from ptcg.core.enums import Stage
    c.stage = Stage.BASIC
    switch_pokemon(p.active[0], c, p)
    assert not getattr(s, "field_event_queue", [])


def test_poison_persists_evolution_and_devolution_only_with_opponent_muk():
    s, p, o = board()
    ability(o.bench[0], "Poison Sacs", {"kind": "persistent_poison"})
    lower = p.active[0]
    lower.poisoned, lower.poison_damage = True, 30
    lower.hp -= 20
    card = registry.get("P01-006")()
    zone(p, "hand", [card])
    reduce_evolve_pokemon_action(EvolvePokemonAction(p.id, card, lower), s)
    assert card.poisoned and card.poison_damage == 30
    result = devolve(card, p, "hand", s)
    assert result is lower and result.poisoned and result.poison_damage == 30
    assert type(lower)().hp - lower.hp == 20
    o.bench[0].ability_blocked_turn = s.turn_number
    reduce_evolve_pokemon_action(EvolvePokemonAction(p.id, card, lower), s)
    assert not getattr(card, "poisoned", False)
    assert not getattr(devolve(card, p, "hand", s), "poisoned", False)


def test_return_active_moves_stack_without_prizes_and_forces_replacement():
    s, p, o = board()
    c = o.active[0]
    energy = registry.get("SVE-008")()
    c.attachment = [energy]
    replacement = o.bench[0]
    action = AttackAction(p.id, p.active[0], p.active[0].attacks[0], c)
    drive(resolve({"operation": "return_field", "destination": "hand"}, action, s))
    assert c in o.hand and energy in o.hand and o.active == [replacement]
    assert len(p.prize) == 6


def test_devolution_keeps_attachments_and_damage_but_clears_attack_effects():
    s, p, o = board()
    lower = p.active[0]
    lower.attack_protection = {"turn": s.turn_number, "damage": True}
    energy = registry.get("SVE-008")()
    lower.attachment = [energy]
    card = registry.get("P01-006")()
    zone(p, "hand", [card])
    reduce_evolve_pokemon_action(EvolvePokemonAction(p.id, card, lower), s)
    card.hp -= 40
    result = devolve(card, p, "left", s)
    assert result.attachment == [energy]
    assert result.energy == energy.provides
    assert type(result)().hp - result.hp == 40
    assert not hasattr(result, "attack_protection")
    assert card in p.left and not card.evolved and not card.attachment

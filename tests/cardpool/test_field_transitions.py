import pytest
from ptcg.core.action import AttackAction, EvolvePokemonAction, UseItemAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType
from ptcg.core.reducer import reduce_evolve_pokemon_action
from ptcg.utils.utils import switch_pokemon
from packages.rules.end_phase import finish_pending
from packages.rules.evolution_effects import devolve
from packages.rules.field_events import finish
from packages.rules.remaining_attacks import resolve
from packages.rules import technical_machines
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
    assert result.firstTurnPlayed


@pytest.mark.parametrize("use_candy", [False, True])
def test_opponents_devolution_allows_evolution_on_your_next_turn(use_candy):
    state, attacker, defender = board()
    impidimp = registry.get("P4P-803EE52253F2")()
    morgrem = registry.get("P4P-2C7FC084F5E6")()
    grimmsnarl = registry.get("P4P-5485406C2C86")()
    candy = registry.get("P4T-4960CF6C611B")()
    target_zone = "bench" if use_candy else "active"
    zone(defender, target_zone, [impidimp])
    zone(defender, "hand", [morgrem, grimmsnarl, candy])
    state.turn = defender.id
    reduce_evolve_pokemon_action(EvolvePokemonAction(defender.id, morgrem, impidimp), state)
    if not use_candy:
        reduce_evolve_pokemon_action(EvolvePokemonAction(defender.id, grimmsnarl, morgrem), state)
    state.turn = attacker.id

    tool = registry.get("P4T-DDB79F5FA206")()
    holder = attacker.active[0]
    holder.attachment = [tool]
    holder.energy = [CardType.ANY]
    attack, = technical_machines.actions(tool, state)
    drive(technical_machines.resolve(tool, attack, state))
    drive(finish_pending(state))
    assert state.turn == defender.id

    target = impidimp if use_candy else morgrem
    assert getattr(defender, target_zone) == [target]
    actions = defender.get_actions(state)
    if use_candy:
        action = next(a for a in actions if isinstance(a, UseItemAction) and a.source is candy)

        def choose(choices, info, count):
            if count <= 2:
                assert choices.candidates == [[grimmsnarl], [target]][count - 1]
                return choices.action_for_candidate_indices([0])
            return next(option for option in choices if option.value == 0)

        drive(candy.reduce_action(action, state), choose)
        assert candy in defender.discard
    else:
        action = next(a for a in actions if isinstance(a, EvolvePokemonAction)
                      and a.source is grimmsnarl and a.target is target)
        reduce_evolve_pokemon_action(action, state)
    assert getattr(defender, target_zone) == [grimmsnarl]
    assert target in grimmsnarl.evolved

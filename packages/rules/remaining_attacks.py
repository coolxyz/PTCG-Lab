"""Composed attack effects sharing explicit protection and choice semantics."""

from ptcg.core.enums import CardPosition, Stage, EnergyType
from ptcg.core.card import EnergyCard, PokemonCard
from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards
from packages.rules.entry_effects import choice
from packages.rules.protection import blocked

OPERATIONS = {"delayed", "knockout", "discard_self", "mill_choose", "peek_shuffle", "shuffle_draw", "top_choice", "counters_filtered", "recoil_counters", "mill_energy", "optional_draw", "discard_down_draw", "conditional_effects", "return_field", "devolve", "evolve_field", "hand_discard", "low_energy_lock", "switch_then", "attach_heal", "return_search", "remaining_recoil", "take_prizes", "win_one_prize", "discard_defender", "knockout_protection"}


def resolve(r, action, state):
    p, o = current_player(state), opponent_player(state)
    source, target = action.source, action.target
    op = r["operation"]
    if op == "hand_discard":
        from packages.rules.trainer_operations import select
        owner = o if r.get("opponent") else p
        count = r.get("count", 1)
        if r.get("evolvedFrom") and getattr(source, "evolved_turn", None) == state.turn_number and getattr(source, "evolved_from_name", None) == r["evolvedFrom"]:
            count += r["bonus"]
        cards = yield from select(source, list(owner.hand), state, owner, count, count)
        move_cards(cards, (owner.id, CardPosition.HAND), (owner.id, CardPosition.DISCARD), state)
    elif op == "low_energy_lock":
        o.low_energy_attack_lock = {"turn": state.turn_number + 1, "maximum": r["maximum"]}
    elif op == "switch_then":
        if not p.bench:
            return
        from ptcg.utils.utils import switch_pokemon
        new = (yield from choice(source, list(p.bench), state))[0]
        switch_pokemon(source, new, p)
        from packages.rules.plain import PlainPokemon
        for child in r["effects"]:
            yield from PlainPokemon.resolve_mechanic(source, child, action, state)
    elif op == "attach_heal":
        from packages.rules.attachment_effects import resolve as attach
        from packages.rules.healing import value
        recipients = yield from attach(source, r["attachment"], state)
        for card in set(recipients):
            card.hp = value(card, r.get("amount"), state, record=True)
    elif op == "return_search":
        from packages.rules.field_moves import return_stack
        return_stack(source, p, "left")
        cards = yield from choice(source, list(p.left), state, 0, r["count"])
        move_cards(cards, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
        shuffle_cards(p.left, state)
        action.group_damage_targets = []
    elif op == "remaining_recoil":
        if not blocked(target, state, "counters", source) and target.hp > r["remaining"]:
            target.hp = r["remaining"]
            from packages.rules.damage_events import deal
            from packages.rules.core_fixes import shield_damage
            deal(source, source, shield_damage(source, r["recoil"], state, source), state)
        action.group_damage_targets = []
    elif op == "take_prizes":
        if r.get("handCount") is not None and len(p.hand) != r["handCount"]:
            return
        count = min(r["count"], len(p.prize))
        if count:
            from packages.rules.prizes import take
            yield from take(p, count, state, source)
            if not p.prize:
                from ptcg.core.exceptions import GameTermination
                state.termination_reason, state.group_winner = "attack_prizes", p.id
                raise GameTermination
            if r.get("shuffleHand"):
                move_cards(list(p.hand), (p.id, CardPosition.HAND), (p.id, CardPosition.LEFT), state)
                shuffle_cards(p.left, state)
    elif op == "win_one_prize":
        if len(p.prize) == 1:
            from ptcg.core.exceptions import GameTermination
            state.termination_reason, state.group_winner = "victory_symbol", p.id
            raise GameTermination
    elif op == "discard_defender":
        if not blocked(target, state, "effects", source):
            from packages.rules.core_fixes import discard_pokemon
            discard_pokemon(o, target)
            action.group_damage_targets = []
    elif op == "knockout_protection":
        if getattr(action, "damage_dealt", 0) > 0 and target.hp <= 0:
            source.pending_knockout_protection = target
            action.group_damage_targets = [target]
    elif op == "return_field":
        from packages.rules.field_moves import return_stack
        if r.get("both"):
            selected = [(p, c) for c in p.active] + [(o, c) for c in o.active]
        else:
            pool = list(o.active if r.get("zone", "active") == "active" else o.bench)
            if r.get("keep"):
                kept = yield from choice(source, pool, state, r["keep"], r["keep"])
                pool = [c for c in pool if c not in kept]
            elif r.get("count"):
                pool = yield from choice(source, pool, state, r["count"], r["count"])
            selected = [(o, c) for c in pool]
        for owner, card in selected:
            if not blocked(card, state, "effects", source):
                return_stack(card, owner, r["destination"])
        if r["destination"] == "left":
            for owner in (p, o) if r.get("both") else (o,):
                shuffle_cards(owner.left, state)
        from packages.rules.knockouts import resolve_group
        yield from resolve_group(state)
    elif op == "devolve":
        from packages.rules.evolution_effects import devolve
        pool = [c for c in (o.active if r.get('activeOnly') else o.active + o.bench) if getattr(c, "evolved", [])]
        if not r.get("all"):
            pool = yield from choice(source, pool, state)
        for card in pool:
            if not blocked(card, state, "effects", source):
                devolve(card, o, r["destination"], state)
        if r["destination"] == "left":
            shuffle_cards(o.left, state)
        action.group_damage_targets = []
    elif op == "evolve_field":
        from ptcg.core.action import EvolvePokemonAction
        from ptcg.core.reducer import reduce_evolve_pokemon_action
        from packages.rules.pokemon_types import has_type
        pool = list(p.bench if r.get("zone") == "bench" else p.active + p.bench)
        if r.get("type"):
            pool = [c for c in pool if has_type(c, r["type"])]
        if not r.get("all"):
            pool = yield from choice(source, pool, state, 0, r.get("count", 1))
        for card in pool:
            candidates = [c for c in p.left if permitted(c) and getattr(c, "evolveFrom", [])[:1] == [card.name]]
            chosen = yield from choice(source, candidates, state, 0, 1)
            if chosen:
                evolution = chosen[0]
                move_cards(evolution, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
                reduce_evolve_pokemon_action(EvolvePokemonAction(p.id, evolution, card), state)
        shuffle_cards(p.left, state)
    elif op == "delayed":
        if not blocked(target, state, "effects", source):
            target.delayed_attacks = [{"turn": state.turn_number+1, "delayed": True, "name": action.attack.name, **r["effect"]}]
    elif op == "knockout":
        from packages.rules.attack_math import counters
        pool = list(o.active if r.get("zone", "active") == "active" else o.bench if r["zone"] == "bench" else o.active+o.bench)
        pool = [c for c in pool if (not r.get("basic") or c.stage == Stage.BASIC)
                and ("counters" not in r or counters(c) == r["counters"])
                and ("minimumCounters" not in r or counters(c) >= r["minimumCounters"])
                and (not r.get("specialEnergy") or any(isinstance(e, EnergyCard) and e.energyType == EnergyType.SPECIAL for e in c.attachment))]
        if r.get("select"):
            pool = yield from choice(source, pool, state)
        for c in pool:
            if not blocked(c, state, "effects", source):
                c.hp = 0
        if r.get("self"):
            source.hp = 0
        action.group_damage_targets = []
    elif op == "discard_self":
        from packages.rules.core_fixes import discard_pokemon
        discard_pokemon(p, source)
        action.group_damage_targets = [target] if getattr(action, "damage_dealt", 0) else []
        if p.bench and not p.active:
            from ptcg.core.reducer import _force_active_replacement
            yield from _force_active_replacement(p, state, p.id)
    elif op == "mill_choose":
        cards = list(p.left[:r["count"]])
        move_cards(cards, (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD), state)
        selected = yield from choice(source, cards, state)
        from packages.rules.entry_effects import reveal
        reveal(selected, state, p)
        move_cards(selected, (p.id, CardPosition.DISCARD), (p.id, CardPosition.HAND), state)
    elif op == "peek_shuffle":
        # A private inspection is an explicit choice; no public reveal event.
        if o.left:
            yield from choice(source, list(o.left[:1]), state)
            if (yield from choice(source, [source], state, 0, 1)):
                shuffle_cards(o.left, state)
    elif op == "shuffle_draw":
        move_cards(list(p.hand), (p.id, CardPosition.HAND), (p.id, CardPosition.LEFT), state)
        shuffle_cards(p.left, state)
        count = len(p.bench)+len(o.bench) if r.get("benches") else r["count"]
        move_cards(list(p.left[:count]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
    elif op == "top_choice":
        window = list(p.left[:r["count"]])
        if window:
            yield from choice(source, window, state, len(window), len(window))
            take = yield from choice(source, [source], state, 0, 1)
            move_cards(window, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND if take else CardPosition.DISCARD), state)
            if not take:
                move_cards(list(p.left[:r["draw"]]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
    elif op == "counters_filtered":
        from packages.rules.attack_math import counters
        from packages.rules.abilities import enabled
        pool = o.bench if r.get("bench") else o.active+o.bench
        if r.get("both"):
            pool = list(pool)+p.active+p.bench
        amount = r["amount"] * (sum(c.name == r["perName"] for c in p.active+p.bench) if r.get("perName") else 1)
        for c in pool:
            if r.get("damaged") and not counters(c) or r.get("ability") and not (getattr(c, "ability", []) and enabled(c, state)):
                continue
            if not blocked(c, state, "counters", source):
                c.hp -= amount
        action.group_damage_targets = [target] if getattr(action, "damage_dealt", 0) else []
    elif op == "recoil_counters":
        from packages.rules.attack_math import counters
        from packages.rules.core_fixes import shield_damage
        from packages.rules.damage_events import deal
        deal(source, source, shield_damage(source, counters(source)*r["factor"], state, source), state)
    elif op == "mill_energy":
        count = sum(energy_matches(e, r["type"]) for e in source.energy)
        move_cards(list(o.left[:count]), (o.id, CardPosition.LEFT), (o.id, CardPosition.DISCARD), state)
    elif op == "optional_draw":
        if p.left and (yield from choice(source, [source], state, 0, 1)):
            move_cards(list(p.left[:r["count"]]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
    elif op == "discard_down_draw":
        discard = len(p.hand) <= 4 or (yield from choice(source, [source], state, 0, 1))
        cards = (yield from choice(source, list(p.hand), state, max(0, len(p.hand)-4), len(p.hand))) if discard else []
        move_cards(cards, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
        move_cards(list(p.left[:max(0, 5-len(p.hand))]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
    elif op == "conditional_effects":
        from packages.rules.attack_math import damage
        from packages.rules.plain import PlainPokemon
        condition = r["condition"]
        n = damage({**condition, "mode": "multiply", "factor": 1}, action, state)
        if n:
            for child in r["effects"]:
                yield from PlainPokemon.resolve_mechanic(source, child, action, state)

from packages.rules.energy_units import matches as energy_matches

from packages.rules.pokemon_replacement import permitted

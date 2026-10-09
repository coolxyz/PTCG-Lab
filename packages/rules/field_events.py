"""Choices triggered by entering the Active Spot, before the turn advances."""

from ptcg.core.enums import Stage, CardType
from ptcg.core.card import EnergyCard
from ptcg.utils.utils import current_player, opponent_player, switch_pokemon
from packages.rules.abilities import enabled
from packages.rules.effects import NumberOption


def moved(player, old, state):
    if player.id != state.turn:
        return
    returned = [c for c in old if c in player.bench]
    if returned:
        other = state.player2 if player is state.player1 else state.player1
        if any(enabled(c, state) and any(r["kind"] == "lava_zone" for r in (getattr(c, "spec", None) or {}).get("abilities", [])) for c in other.active + other.bench):
            for c in player.active:
                c.burned = True
        for card in returned:
            if not enabled(card, state):
                continue
            for rule in (getattr(card, "spec", None) or {}).get("abilities", []):
                if rule["kind"] == "zero_to_hero":
                    state.field_event_queue = getattr(state, "field_event_queue", []) + [(player, card, {**rule, "effect": {"kind": "replace", "name": "Palafin ex"}})]
    for card in player.active:
        if card in old or not enabled(card, state):
            continue
        for rule in (getattr(card, "spec", None) or {}).get("abilities", []):
            if rule["kind"] == "move_active" and getattr(card, "move_ability_turn", None) != state.turn_number:
                queue = getattr(state, "field_event_queue", [])
                state.field_event_queue = queue + [(player, card, dict(rule))]


def finish(state):
    if getattr(state, "resolving_field_events", False):
        return
    state.resolving_field_events = True
    try:
        while getattr(state, "field_event_queue", []):
            p, card, rule = state.field_event_queue.pop(0)
            if card not in p.active+p.bench or getattr(card, "move_ability_turn", None) == state.turn_number:
                continue
            # Availability was captured at the transition. A later switch by
            # the same attack cannot retroactively cancel a triggered ability.
            options = [NumberOption(p, 0, "不使用"), NumberOption(p, 1, rule.get("name", "使用换位特性"))]
            selected = yield (state.get_obs(p.id), 0, False, {"raw_available_actions": options})
            if not selected.value:
                continue
            card.move_ability_turn = state.turn_number
            from packages.rules.entry_effects import effect, choice, transfer
            op = rule["effect"]["kind"]
            r = rule["effect"]
            if op == "replace":
                from packages.rules.pokemon_replacement import choose_replace
                yield from choose_replace(card, p, state, r["name"])
            elif op == "gust_basic":
                other = opponent_player(state)
                cards = yield from choice(card, [c for c in other.bench if c.stage == Stage.BASIC], state)
                from packages.rules.ability_protection import blocked
                if cards and not blocked(other.active[0],card,state) and not blocked(cards[0],card,state):
                    switch_pokemon(other.active[0], cards[0], other)
            elif op == "gather_energy":
                donors = [(c, e) for c in p.active+p.bench if c is not card for e in c.attachment
                          if isinstance(e, EnergyCard) and any(energy_matches(t, r["type"]) for t in e.provides)]
                cards = yield from choice(card, [e for _,e in donors], state, 0, len(donors))
                for donor, energy in donors:
                    if energy in cards:
                        transfer(donor, card, [energy])
            else:
                yield from effect(card, r, state)
    finally:
        state.resolving_field_events = False

from packages.rules.energy_units import matches as energy_matches

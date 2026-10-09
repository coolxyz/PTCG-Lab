"""Stateful field changes settle after damage and before knockout prizes."""

from ptcg.core.action import choose_card_actions, EvolvePokemonAction
from ptcg.core.reducer import (
    reduce_choose_card_actions,
    reduce_evolve_pokemon_action,
    _force_active_replacement,
)
from ptcg.core.enums import Stage, CardPosition
from ptcg.utils.utils import (
    current_player,
    opponent_player,
    move_cards,
    switch_pokemon,
    shuffle_cards,
)
from packages.rules.entry_effects import choice
from packages.rules.protection import blocked


def resolve(rule, action, state):
    p, o = current_player(state), opponent_player(state)
    source, target = action.source, action.target
    kind = rule["operation"]
    if rule.get("optional"):
        # Selection of the physical source is an explicit yes/no prompt.
        if not (yield from choice(source, [source], state, 0, 1)):
            return
    if kind == "return_self":
        from packages.rules.field_moves import return_stack

        return_stack(source, p, rule["destination"])
        if rule["destination"] == "left":
            shuffle_cards(p.left, state)
        action.group_damage_targets = (
            [target] if getattr(action, "damage_dealt", 0) else []
        )
        if not p.active and p.bench:
            yield from _force_active_replacement(p, state, p.id)
    elif kind == "evolve_self":
        pool = [c for c in p.left if permitted(c) and getattr(c, "evolveFrom", [])[:1] == [source.name]]
        selected = yield from choice(source, pool, state, 0, 1)
        if selected:
            card = selected[0]
            move_cards(
                card, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state
            )
            reduce_evolve_pokemon_action(EvolvePokemonAction(p.id, card, source), state)
        shuffle_cards(p.left, state)
    elif kind == "double_switch":
        if not p.bench:
            return
        replacement = (yield from choice(source, list(p.bench), state))[0]
        switch_pokemon(source, replacement, p)
        if o.bench and not blocked(target, state, "effects", source):
            replacement = (
                yield from reduce_choose_card_actions(
                    choose_card_actions(o.id, o.id, 1, 1, list(o.bench), source=source),
                    state,
                )
            )[0]
            switch_pokemon(target, replacement, o)
    elif kind == "retaliate":
        source.damage_retaliation = {
            "kind": "retaliate",
            "counters": rule.get("counters",0),
            "equalDamage": rule.get("equalDamage",False),
            "anyZone": True,
            "turn": state.turn_number + 1,
        }
    elif kind == "opponent_reset":
        move_cards(
            list(o.hand), (o.id, CardPosition.HAND), (o.id, CardPosition.LEFT), state
        )
        shuffle_cards(o.left, state)
        move_cards(
            list(o.left[: rule["count"]]),
            (o.id, CardPosition.LEFT),
            (o.id, CardPosition.HAND),
            state,
        )
    elif not blocked(target, state, "effects", source):
        if kind == "reduce_damage":
            target.attack_damage_reduction = {
                "turn": state.turn_number + 1,
                "amount": rule["amount"],
            }
        elif kind == "conditional_lock":
            if (target.stage == Stage.BASIC) == (rule["stage"] == "basic"):
                target.attack_blocked_turn = state.turn_number + 1
        elif kind == "instant_ko":
            target.hp = 0
            # This is damage to the attacker, not placement of damage counters.
            from packages.rules.damage_events import deal
            from packages.rules.core_fixes import shield_damage

            deal(
                source,
                source,
                shield_damage(source, rule["recoil"], state, source),
                state,
            )
            action.group_damage_targets = []
        else:
            raise ValueError(kind)

from packages.rules.pokemon_replacement import permitted

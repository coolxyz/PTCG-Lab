
from packages.rules.maximum_hp import maximum
from ptcg.core.action import choose_card_actions
from packages.rules.modifiers import attack_damage
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.core.enums import Coin, CardPosition
from ptcg.utils.utils import (
    current_player,
    opponent_player,
    flip_coin,
    switch_pokemon,
    move_cards,
)
from packages.rules.core_fixes import shield_damage, discard_card
from packages.rules.damage_events import deal


KINDS = {
    "field_operation",
    "extended_field",
    "item_lock",
    "gust_damage",
    "own_bench_damage",
    "attack_protection",
    "bench_damage",
    "heal_field",
    "mill_self",
    "discard_stadium",
    "recover_status",
    "gust",
    "place_counters",
    "spread_damage",
    "discard_draw",
}


def resolve(mechanic, action, state):
    p, o = current_player(state), opponent_player(state)
    kind = mechanic["kind"]
    if kind == "field_operation":
        from packages.rules.attack_operations import resolve as operation
        yield from operation(mechanic, action, state)
    elif kind == "extended_field":
        from packages.rules.attack_field_effects import resolve as extended
        yield from extended(mechanic, action, state)
    elif kind == "item_lock":
        o.item_blocked_turn = state.turn_number + 1
    elif kind == "gust_damage":
        from packages.rules.protection import blocked

        if not o.bench or (mechanic.get("opponentChooses") and blocked(action.target, state, "effects", action.source)):
            return
        chooser = o if mechanic.get("opponentChooses") else p
        chosen = (
            yield from reduce_choose_card_actions(
                choose_card_actions(
                    chooser.id, chooser.id, 1, 1, list(o.bench), source=action.source
                ),
                state,
            )
        )[0]
        if not mechanic.get("opponentChooses") and blocked(chosen, state, "effects", action.source):
            return
        switch_pokemon(o.active[0], chosen, o)
        from ptcg.core.reducer import _calculate_damage

        amount = _calculate_damage(action.source, chosen, mechanic["amount"], state)
        deal(action.source, chosen, amount, state)
        p.reward.apply_damage_dealt_reward(amount)
        action.group_damage_targets = [chosen]
        if mechanic.get("paralyzeCoin") and flip_coin(state) == Coin.HEAD and not blocked(chosen, state, "effects", action.source):
            from packages.rules.status_immunity import apply_status
            from ptcg.core.enums import SpecialCondition
            apply_status(chosen, SpecialCondition.PARALYZED)
    elif kind == "own_bench_damage":
        targets = list(p.bench)
        if mechanic.get("select") and targets:
            targets = yield from reduce_choose_card_actions(choose_card_actions(p.id, p.id, 1, 1, targets, source=action.source), state)
        for card in targets:
            deal(action.source, card, shield_damage(card, mechanic["amount"], state, action.source), state)
        action.group_damage_targets = [action.target] + targets
    elif kind == "attack_protection":
        if not mechanic.get("coin") or flip_coin(state) == Coin.HEAD:
            action.source.attack_protection = {
                **mechanic,
                "turn": state.turn_number + 1,
            }
    elif kind == "discard_draw":
        move_cards(
            list(p.hand), (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state
        )
        move_cards(
            list(p.left[: mechanic["count"]]),
            (p.id, CardPosition.LEFT),
            (p.id, CardPosition.HAND),
            state,
        )
    elif kind in ("place_counters", "spread_damage"):
        targets = list(o.bench if mechanic["zone"] == "bench" else o.active if mechanic["zone"] == "active" else o.active + o.bench)
        if mechanic.get("select") and targets:
            targets = yield from reduce_choose_card_actions(
                choose_card_actions(p.id, p.id, 1, 1, targets, source=action.source),
                state,
            )
        for target in targets:
            from packages.rules.protection import blocked

            if kind == "place_counters" and blocked(
                target, state, "counters", action.source
            ):
                continue
            amount = mechanic["amount"]
            if kind == "spread_damage":
                from ptcg.core.reducer import _calculate_damage

                amount = (
                    _calculate_damage(action.source, target, amount, state)
                    if target in o.active
                    else shield_damage(target, attack_damage(action.source, target, amount, state), state, action.source)
                )
                p.reward.apply_damage_dealt_reward(amount)
            if kind == "spread_damage":
                deal(action.source, target, amount, state)
            else:
                target.hp -= amount
        action.group_damage_targets = (
            [action.target] if action.attack.damage else []
        ) + (targets if kind == "spread_damage" else [])
    elif kind == "recover_status":
        for attr in ("special_condition", "poisoned", "burned", "poison_damage"):
            if hasattr(action.source, attr):
                delattr(action.source, attr)
    elif kind == "mill_self":
        move_cards(
            list(p.left[: mechanic["count"]]),
            (p.id, CardPosition.LEFT),
            (p.id, CardPosition.DISCARD),
            state,
        )
    elif kind == "discard_stadium":
        selected = list(state.stadium)
        if selected and mechanic.get("optional"):
            selected = yield from reduce_choose_card_actions(
                choose_card_actions(p.id, p.id, 0, 1, selected, source=action.source),
                state,
            )
        for stadium in selected:
            owner = (
                state.player1
                if stadium.playedFrom == state.player1.id
                else state.player2
            )
            state.stadium.remove(stadium)
            discard_card(owner, stadium)
    elif kind == "heal_field":
        targets = [c for c in (p.bench if mechanic.get("zone") == "bench" else p.active + p.bench) if c.hp < maximum(c) and (not mechanic.get("trait") or c.pokemonRule.name == mechanic["trait"])]
        if mechanic["target"] == "one" and targets:
            targets = yield from reduce_choose_card_actions(
                choose_card_actions(p.id, p.id, 1, 1, targets, source=action.source),
                state,
            )
        for target in targets:
            target.hp = healed(target, None if mechanic.get("full") else mechanic["amount"], state, record=True)
    elif kind == "gust":
        from packages.rules.protection import blocked

        if (not mechanic["coin"] or flip_coin(state) == Coin.HEAD) and o.bench:
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id, p.id, 1, 1, list(o.bench), source=action.source
                ),
                state,
            )
            if not blocked(chosen[0], state, "effects", action.source):
                switch_pokemon(o.active[0], chosen[0], o)
    elif kind == "bench_damage":
        candidates = [c for c in o.bench if not mechanic.get('damaged') or c.hp < maximum(c)]
        if candidates:
            count = min(mechanic.get("count", 1), len(candidates))
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id, p.id, count, count, candidates, source=action.source
                ),
                state,
            )
            for target in chosen:
                amount = shield_damage(target, attack_damage(action.source, target, mechanic["amount"], state), state, action.source)
                deal(action.source, target, amount, state)
                p.reward.apply_damage_dealt_reward(amount)
            action.group_damage_targets = [action.target] + list(chosen)

from packages.rules.healing import value as healed

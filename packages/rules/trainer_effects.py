"""Shared trainer selections; private searches and public disclosures stay separate."""

from ptcg.core.action import choose_card_actions
from ptcg.core.enums import CardPosition, SpecialCondition
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards

KINDS = {
    "trainer_operation",
    "look_hand",
    "discard_hand_to",
    "opponent_hand",
    "reveal_draw",
    "opponent_status",
}


def playable(rule, p, o):
    from packages.rules.trainer_field_effects import KINDS as FIELD_KINDS, playable as field_playable
    if rule["kind"] in FIELD_KINDS:
        return field_playable(rule, p, o)
    kind = rule["kind"]
    if kind == "trainer_operation":
        from packages.rules.trainer_operations import playable as operation_playable
        return operation_playable(rule, p, o)
    if kind == "look_hand":
        return bool(p.left)
    if kind == "discard_hand_to":
        return len(o.hand) > rule["count"] or (
            rule["players"] == "both" and len(p.hand) - 1 > rule["count"]
        )
    if kind in ("opponent_hand", "reveal_draw"):
        return bool(o.hand)
    if kind == "opponent_status":
        return bool(o.active) and not (
            getattr(o.active[0], "burned", False)
            and getattr(o.active[0], "special_condition", None)
            == SpecialCondition.CONFUSED
        )
    return True


def reveal(player, cards, state):
    if cards:
        state.public_reveals.append(
            {
                "kind": "search_reveal",
                "actor": player.id.name,
                "cards": [c.to_dict() for c in cards],
            }
        )


def resolve(source, rule, state):
    from packages.rules.trainers import matches

    p, o = current_player(state), opponent_player(state)
    kind = rule["kind"]
    if kind == "trainer_operation":
        from packages.rules.trainer_operations import resolve as operation_resolve
        yield from operation_resolve(source, rule, state)
        return
    if kind == "look_hand":
        window = list(p.left[: rule["look"]])
        pool = [c for c in window if matches(c, rule.get("filter", "any"))]
        count = min(rule["count"], len(pool))
        chosen = []
        if count:
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id,
                    p.id,
                    0 if rule.get("optional") else count,
                    count,
                    pool,
                    source=source,
                ),
                state,
            )
            move_cards(
                chosen, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state
            )
        if rule.get("reveal"):
            reveal(p, chosen, state)
        rest = [c for c in window if c not in chosen]
        if rule["rest"] == "discard":
            move_cards(
                rest, (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD), state
            )
        elif rule["rest"] == "bottom":
            shuffle_cards(rest, state)
            move_cards(
                rest, (p.id, CardPosition.LEFT), (p.id, CardPosition.LEFT), state
            )
        else:
            shuffle_cards(p.left, state)
    elif kind == "discard_hand_to":
        for player in (o, p) if rule["players"] == "both" else (o,):
            count = max(0, len(player.hand) - rule["count"])
            if count:
                chosen = yield from reduce_choose_card_actions(
                    choose_card_actions(
                        player.id,
                        player.id,
                        count,
                        count,
                        list(player.hand),
                        source=source,
                    ),
                    state,
                )
                move_cards(
                    chosen,
                    (player.id, CardPosition.HAND),
                    (player.id, CardPosition.DISCARD),
                    state,
                )
    elif kind == "opponent_hand":
        reveal(o, list(o.hand), state)
        pool = [c for c in o.hand if matches(c, rule["filter"])]
        count = min(rule["count"], len(pool))
        if count:
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id,
                    p.id,
                    0 if rule.get("optional") else count,
                    count,
                    pool,
                    source=source,
                ),
                state,
            )
            destination = (
                CardPosition.DISCARD
                if rule["destination"] == "discard"
                else CardPosition.LEFT
            )
            move_cards(chosen, (o.id, CardPosition.HAND), (o.id, destination), state)
    elif kind == "reveal_draw":
        reveal(o, list(o.hand), state)
        count = sum(matches(c, rule["filter"]) for c in o.hand) * rule["factor"]
        move_cards(
            list(p.left[:count]),
            (p.id, CardPosition.LEFT),
            (p.id, CardPosition.HAND),
            state,
        )
    elif kind == "opponent_status":
        for status in rule["statuses"]:
            if status in ("BURNED", "POISONED"):
                setattr(o.active[0], status.lower(), True)
                if status == "POISONED":
                    o.active[0].poison_damage = 10
            else:
                o.active[0].special_condition = SpecialCondition[status]

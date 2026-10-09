"""Reviewed zone operations shared by data-driven attack effects.

The caller supplies the physical source (including copied attacks). Operations
resolve after attack damage, before knockouts, and never end the turn themselves.
"""

from packages.rules.maximum_hp import maximum


from ptcg.core.action import choose_card_actions
from ptcg.core.card import (
    PokemonCard,
    EnergyCard,
    ToolCard,
    StadiumCard,
    SupporterCard,
    ItemCard,
)
from ptcg.core.enums import CardPosition, EnergyType, Stage, CardType
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import (
    current_player,
    opponent_player,
    move_cards,
    shuffle_cards,
    switch_pokemon,
)
from packages.rules.core_fixes import discard_card, refresh_energy


KINDS = {
    "attach_multiple",
    "recover_hand",
    "recover_deck",
    "move_self_energy",
    "return_self_energy",
    "reveal_opponent_hand",
    "random_discard_hand",
    "search_attach",
    "draw_until",
    "heal_self",
    "mill_opponent",
    "discard_self_energy",
    "discard_opponent_energy",
    "self_switch",
    "search_bench",
    "search_hand",
}


def matches(card, kind):
    if kind == "pokemon_or_energy":
        return matches(card,"pokemon") or matches(card,"basic_energy")
    if kind.startswith("prefix:"):
        return isinstance(card,PokemonCard) and card.name.startswith(kind[7:])
    if kind.startswith("pokemon:"):
        return isinstance(card,PokemonCard) and has_type(card, kind[8:])
    if kind == "item_or_tool":
        return isinstance(card, (ItemCard, ToolCard))
    if kind == "trainer":
        return isinstance(card, (ItemCard, SupporterCard, ToolCard, StadiumCard))
    if kind == "any":
        return True
    if kind.startswith("basic_energy:"):
        return (
            matches(card, "basic_energy")
            and has_type(card, CardType[kind.split(":", 1)[1]])
        )
    if kind.startswith("name:"):
        return card.name == kind[5:]
    if kind == "item":
        return isinstance(card, ItemCard)
    if kind == "supporter":
        return isinstance(card, SupporterCard)
    if kind == "pokemon":
        return isinstance(card, PokemonCard)
    if kind == "basic_energy":
        return isinstance(card, EnergyCard) and card.energyType == EnergyType.BASIC
    if kind == "energy":
        return isinstance(card, EnergyCard)
    if kind == "tool":
        return isinstance(card, ToolCard)
    if kind == "stadium":
        return isinstance(card, StadiumCard)
    raise ValueError("Unknown reviewed search filter: " + kind)


def discard_attached(target, chosen, owner):
    state = getattr(owner, "rules_state", None)
    if state is not None:
        from packages.rules.zone_guards import trainer_immune
        if trainer_immune(target, state):
            return
    for card in chosen:
        target.attachment.remove(card)
        discard_card(owner, card)
    target.energy = [
        e
        for card in target.attachment
        if isinstance(card, EnergyCard)
        for e in card.provides
    ]
    refresh_energy(target)
    for i, card in enumerate(target.attachment):
        card.index = i + 1


def resolve(mechanic, action, state):
    p = current_player(state)
    o = opponent_player(state)
    source, kind = action.source, mechanic["kind"]
    if kind == "attach_multiple":
        from packages.rules.attachment_effects import resolve as attach

        yield from attach(source, mechanic, state)
    elif kind in ("recover_hand","recover_deck"):
        cards = [c for c in p.discard if matches(c, mechanic["filter"])]
        n = min(mechanic["count"], len(cards))
        if n:
            selected = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id,
                    p.id,
                    0 if mechanic.get("optional") else n,
                    n,
                    cards,
                    source=source,
                ),
                state,
            )
            move_cards(
                selected, (p.id, CardPosition.DISCARD), (p.id, CardPosition.LEFT if kind == "recover_deck" else CardPosition.HAND), state
            )
            if kind == "recover_deck":
                shuffle_cards(p.left,state)
            if selected:
                state.public_reveals.append(
                    {
                        "kind": "search_reveal",
                        "actor": p.id.name,
                        "cards": [c.to_dict() for c in selected],
                    }
                )
    elif kind == "reveal_opponent_hand":
        state.public_reveals.append(
            {
                "kind": "hand_reveal",
                "actor": o.id.name,
                "cards": [c.to_dict() for c in o.hand],
            }
        )
    elif kind == "random_discard_hand":
        if o.hand:
            shuffled = list(o.hand)
            shuffle_cards(shuffled, state)
            destination = (
                CardPosition.LEFT
                if mechanic.get("destination") == "deck"
                else CardPosition.DISCARD
            )
            if destination == CardPosition.LEFT:
                state.public_reveals.append(
                    {
                        "kind": "hand_reveal",
                        "actor": o.id.name,
                        "cards": [c.to_dict() for c in shuffled[:mechanic.get("count", 1)]],
                    }
                )
            move_cards(
                shuffled[:mechanic.get("count", 1)],
                (o.id, CardPosition.HAND),
                (o.id, destination),
                state,
            )
            if destination == CardPosition.LEFT:
                shuffle_cards(o.left, state)
    elif kind in ("move_self_energy", "return_self_energy"):
        from packages.rules.zone_guards import hand_return_forbidden
        if kind == "return_self_energy" and hand_return_forbidden(p, state):
            return
        refresh_energy(source)
        cards = [
            c for c in source.attachment if isinstance(c, EnergyCard) and c.provides
        ]
        if not cards or (kind == "move_self_energy" and not p.bench):
            return
        if mechanic.get("all"):
            chosen = cards
        else:
            from packages.rules.energy_selection import choose_units

            chosen = yield from choose_units(
                source, mechanic.get("count", 1), action, state, mechanic.get("type")
            )
        target = None
        if kind == "move_self_energy" and not mechanic.get("distribute"):
            target = (
                yield from reduce_choose_card_actions(
                    choose_card_actions(p.id, p.id, 1, 1, list(p.bench), source=source),
                    state,
                )
            )[0]
        for card in chosen:
            if mechanic.get("distribute"):
                target = (
                    yield from reduce_choose_card_actions(
                        choose_card_actions(
                            p.id, p.id, 1, 1, list(p.bench), source=source
                        ),
                        state,
                    )
                )[0]
            source.attachment.remove(card)
            if target:
                target.attachment.append(card)
                card.cardPosition = CardPosition.BENCH_ATTACHMENT
            else:
                fresh = type(card)()
                vars(card).clear()
                vars(card).update(vars(fresh))
                p.hand.append(card)
                card.cardPosition = CardPosition.HAND
            if target:
                for i, attached in enumerate(target.attachment):
                    attached.index = i + 1
                target.dynamic_energy = True
                refresh_energy(target)
        for collection in (source.attachment, target.attachment if target else p.hand):
            for i, card in enumerate(collection):
                card.index = i + 1
        source.dynamic_energy = True
        refresh_energy(source)
        if target:
            target.dynamic_energy = True
            refresh_energy(target)
    elif kind == "draw_until":
        if mechanic.get("optional"):
            from packages.rules.effects import NumberOption

            choice = yield (
                state.get_obs(p.id),
                0,
                False,
                {"raw_available_actions": [NumberOption(p, 0, "不抽牌"), NumberOption(p, 1, "抽牌")]},
            )
            if choice.value == 0:
                return
        count = max(0, (len(o.hand) if mechanic.get("opponentHand") else mechanic["count"]) - len(p.hand))
        move_cards(
            list(p.left[:count]),
            (p.id, CardPosition.LEFT),
            (p.id, CardPosition.HAND),
            state,
        )
    elif kind == "heal_self":
        source.hp = healed(source, mechanic["amount"], state, record=True)
    elif kind == "mill_opponent":
        move_cards(
            list(o.left[: mechanic["count"]]),
            (o.id, CardPosition.LEFT),
            (o.id, CardPosition.DISCARD),
            state,
        )
    elif kind in ("discard_self_energy", "discard_opponent_energy"):
        from packages.rules.protection import blocked

        if kind == "discard_opponent_energy" and blocked(
            action.target, state, "effects", source
        ):
            return
        target, owner = (
            (source, p) if kind == "discard_self_energy" else (action.target, o)
        )
        cards = [c for c in target.attachment if isinstance(c, EnergyCard)]
        if mechanic["count"] == "all" and mechanic.get("type"):
            refresh_energy(target)
            cards = [c for c in cards if any(energy_matches(e, mechanic["type"]) for e in c.provides)]
        if mechanic["count"] != "all" and (
            mechanic["count"] > 1 or mechanic.get("type")
        ):
            from packages.rules.energy_selection import choose_units

            chosen = yield from choose_units(
                target, mechanic["count"], action, state, mechanic.get("type")
            )
            discard_attached(target, chosen, owner)
            return
        limit = (
            len(cards)
            if mechanic["count"] == "all"
            else min(mechanic["count"], len(cards))
        )
        if limit:
            if limit == len(cards):
                chosen = cards
            else:
                chosen = yield from reduce_choose_card_actions(
                    choose_card_actions(
                        p.id,
                        p.id,
                        limit,
                        limit,
                        cards,
                        source=source,
                        tips="Choose attached Energy cards to discard.",
                    ),
                    state,
                )
            discard_attached(target, chosen, owner)
    elif kind == "self_switch":
        targets = [c for c in p.bench if not mechanic.get("type") or has_type(c, mechanic["type"])]
        if targets:
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id,
                    p.id,
                    0 if mechanic["optional"] else 1,
                    1,
                    targets,
                    source=source,
                    tips="Choose your new Active Pokemon.",
                ),
                state,
            )
            if chosen:
                switch_pokemon(source, chosen[0], p)
    elif kind == "search_attach":
        cards = [
            c
            for c in p.left
            if matches(c, "basic_energy")
            and (not mechanic.get("type") or has_type(c, CardType[mechanic["type"]]))
        ]
        if cards:
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id,
                    p.id,
                    0,
                    min(mechanic["count"], len(cards)),
                    cards,
                    source=source,
                    tips="Search for basic Energy to attach.",
                ),
                state,
            )
            for energy in chosen:
                target = source
                if mechanic["target"] == "any":
                    selected = yield from reduce_choose_card_actions(
                        choose_card_actions(
                            p.id,
                            p.id,
                            1,
                            1,
                            list(p.active + p.bench),
                            source=source,
                            tips="Choose a Pokemon for this Energy.",
                        ),
                        state,
                    )
                    target = selected[0]
                zone = (
                    CardPosition.ACTIVE_ATTACHMENT
                    if target in p.active
                    else CardPosition.BENCH_ATTACHMENT
                )
                move_cards(
                    energy, (p.id, CardPosition.LEFT), (p.id, zone, target.index), state
                )
                target.energy = [
                    e
                    for c in target.attachment
                    if isinstance(c, EnergyCard)
                    for e in c.provides
                ]
                refresh_energy(target)
        shuffle_cards(p.left, state)
    elif kind in ("search_bench", "search_hand"):
        if kind == "search_bench":
            cards = [
                c
                for c in p.left
                if isinstance(c, PokemonCard)
                and (mechanic.get("anyStage") or c.stage == Stage.BASIC)
                and (not mechanic.get("names") or c.name in mechanic["names"])
                and (not mechanic.get("prefix") or c.name.startswith(mechanic["prefix"]))
                and (
                    not mechanic.get("type") or has_type(c, CardType[mechanic["type"]])
                )
            ]
            limit = min(mechanic["count"], max(0, p.benchSize - len(p.bench)))
            destination = CardPosition.BENCH
        else:
            cards = [c for c in p.left if matches(c, mechanic["filter"])]
            limit = len(p.bench) if mechanic.get("countFromBench") else mechanic["count"]
            destination = CardPosition.HAND
        if cards and limit:
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id,
                    p.id,
                    0,
                    min(limit, len(cards)),
                    cards,
                    source=source,
                    tips="Search your deck; you may choose no cards.",
                ),
                state,
            )
            move_cards(chosen, (p.id, CardPosition.LEFT), (p.id, destination), state)
            if destination == CardPosition.HAND and chosen and mechanic.get("reveal", True):
                state.public_reveals.append(
                    {
                        "kind": "search_reveal",
                        "actor": p.id.name,
                        "cards": [c.to_dict() for c in chosen],
                    }
                )
        shuffle_cards(p.left, state)
    else:
        raise ValueError("Unknown reviewed zone effect: " + kind)

from packages.rules.healing import value as healed

from packages.rules.pokemon_types import has_type

from packages.rules.energy_units import matches as energy_matches

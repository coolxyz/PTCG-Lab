"""Optional once-per-entry effects, fired only by a hand-to-field action."""

from ptcg.core.action import choose_card_actions
from ptcg.core.card import EnergyCard
from ptcg.core.enums import CardPosition, CardType, EnergyType, SpecialCondition, Stage
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import (
    current_player,
    opponent_player,
    move_cards,
    shuffle_cards,
    switch_pokemon,
)
from packages.rules.core_fixes import discard_card, refresh_energy
from packages.rules.maximum_hp import maximum, settle, reconcile
from packages.rules.zone_effects import matches as zone_matches, discard_attached
from packages.rules.abilities import enabled


def choice(source, cards, state, minimum=1, maximum_=1):
    p = current_player(state)
    count = min(maximum_, len(cards))
    if not cards or not count:
        return []
    return (
        yield from reduce_choose_card_actions(
            choose_card_actions(
                p.id,
                p.id,
                min(minimum, count),
                count,
                cards,
                indexed=True,
                source=source,
            ),
            state,
        )
    )


def matches(card, category):
    if category.startswith("prefix:"):
        from ptcg.core.card import PokemonCard
        return isinstance(card,PokemonCard) and card.name.startswith(category[7:])
    if category == "colorless_100":
        from ptcg.core.card import PokemonCard
        return isinstance(card,PokemonCard) and has_type(card, CardType.COLORLESS) and type(card)().hp <= 100
    if category.startswith("name:"):
        return card.name == category[5:]
    return zone_matches(card, category)


def reveal(cards, state, actor, kind="search_reveal"):
    if cards:
        state.public_reveals.append(
            {
                "kind": kind,
                "actor": actor.id.name,
                "cards": [c.to_dict() for c in cards],
            }
        )


def resolve(source, trigger, state):
    if not enabled(source, state):
        return
    p = current_player(state)
    for ability in source.spec.get("abilities", []):
        if ability.get("kind") != "on_entry" or ability["trigger"] != trigger:
            continue
        if ability.get("requiresTrait") and not any(c.pokemonRule.name == ability["requiresTrait"] for c in p.active + p.bench):
            continue
        from packages.rules.effects import NumberOption

        if not ability.get('mandatory'):
            decision = yield (
                state.get_obs(p.id),
                0,
                False,
                {"raw_available_actions": [NumberOption(p, 0, "不使用特性"), NumberOption(p, 1, "使用特性")]},
            )
            if decision.value == 0:
                continue
        previous=getattr(state,'effect_context',{})
        state.effect_context={'kind':'ability','source':source,'owner':p.id,'fromHand':False}
        try:
            yield from effect(source, ability["effect"], state)
        finally:
            state.effect_context=previous
        yield from settle(state)


def effect(source, rule, state):
    p, o = current_player(state), opponent_player(state)
    kind = rule["kind"]
    if kind == 'expanded_entry':
        from packages.rules.expanded_entry import resolve
        yield from resolve(source, rule, state)
        return
    from packages.rules.activated_primitives import KINDS, resolve as primitive_resolve
    if kind in KINDS:
        yield from primitive_resolve(source, rule, state)
    elif kind == "counter_buff":
        source.hp -= rule["selfCounters"]
        source.turn_damage_bonus = {"turn": state.turn_number, "amount": rule["damageBonus"]}
    elif kind == "discard_hand_draw":
        move_cards(
            list(p.hand), (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state
        )
        move_cards(
            list(p.left[: rule["count"]]),
            (p.id, CardPosition.LEFT),
            (p.id, CardPosition.HAND),
            state,
        )
    elif kind == "switch_poison":
        selected = yield from choice(source, switch_targets(rule, p), state)
        if selected:
            switch_pokemon(p.active[0], selected[0], p)
            selected[0].poisoned, selected[0].poison_damage = True, 10
    elif kind == "opponent_switch_discard_self":
        if o.active and o.bench:
            selected = yield from reduce_choose_card_actions(
                choose_card_actions(o.id, o.id, 1, 1, list(o.bench), source=source),
                state,
            )
            switch_pokemon(o.active[0], selected[0], o)
            from packages.rules.core_fixes import discard_pokemon

            discard_pokemon(p, source)
    elif kind == "attach":
        from packages.rules.attachment_effects import resolve as attach

        yield from attach(source, rule, state)
    elif kind in ("search", "recover"):
        owner = o if rule.get("opponent") else p
        origin = "left" if kind == "search" else "discard"
        cards = [c for c in getattr(owner, origin) if matches(c, rule["filter"])]
        selected = yield from choice(
            source,
            cards,
            state,
            0 if kind == "search" or rule["count"] > 1 else 1,
            rule["count"],
        )
        destination = (
            CardPosition.DISCARD
            if rule.get("destination") == "discard"
            else CardPosition.HAND
        )
        reveal(selected, state, owner)
        move_cards(
            selected,
            (owner.id, CardPosition[origin.upper()]),
            (owner.id, destination),
            state,
        )
        if kind == "search":
            shuffle_cards(owner.left, state)
    elif kind == "supporter_choice":
        from packages.rules.effects import NumberOption

        decision = yield (
            state.get_obs(p.id),
            0,
            False,
            {"raw_available_actions": [NumberOption(p, 0, "从牌库检索支援者"), NumberOption(p, 1, "从弃牌区回收支援者")]},
        )
        yield from effect(
            source,
            {
                "kind": "search" if decision.value == 0 else "recover",
                "filter": "supporter",
                "count": 1,
            },
            state,
        )
    elif kind == "draw":
        move_cards(
            list(p.left[: rule["count"]]),
            (p.id, CardPosition.LEFT),
            (p.id, CardPosition.HAND),
            state,
        )
    elif kind == "mill":
        move_cards(
            list(o.left[: rule["count"]]),
            (o.id, CardPosition.LEFT),
            (o.id, CardPosition.DISCARD),
            state,
        )
    elif kind == "counters":
        candidates = list(o.bench if rule["zone"] == "bench" else o.active + o.bench)
        targets = yield from choice(
            source, candidates, state, rule["count"], rule["count"]
        )
        for target in targets:
            from packages.rules.ability_protection import blocked
            if not blocked(target,source,state):
                target.hp -= rule["amount"]
    elif kind == "status":
        if o.active:
            from packages.rules.status_immunity import apply_status
            apply_status(o.active[0], SpecialCondition[rule["status"]])
    elif kind == "heal":
        candidates = list(
            p.active if rule["target"] == "active" else p.active + p.bench
        )
        targets = [
            c
            for c in candidates
            if c.hp < maximum(c)
            and (not rule.get("type") or has_type(c, CardType[rule["type"]]))
            and (rule["target"] != "evolved" or c.stage != Stage.BASIC)
            and (rule.get("stage") != "evolved" or c.stage != Stage.BASIC)
        ]
        if rule["target"] == "one":
            targets = yield from choice(source, targets, state)
        for target in targets:
            target.hp = healed(target, rule.get("amount"), state, record=True)
            if rule.get("discardEnergy"):
                discard_attached(
                    target,
                    [c for c in target.attachment if isinstance(c, EnergyCard)],
                    p,
                )
    elif kind == "gust":
        targets = [c for c in o.bench if c.hp <= rule.get("remainingHP", float("inf"))]
        selected = yield from choice(source, targets, state)
        if selected:
            switch_pokemon(o.active[0], selected[0], o)
            if rule.get("status"):
                from packages.rules.status_immunity import apply_status
                apply_status(selected[0], SpecialCondition[rule["status"]])
    elif kind == "discard_stadium":
        for card in list(state.stadium):
            owner = p if card.playedFrom == p.id else o
            state.stadium.remove(card)
            discard_card(owner, card)
    elif kind == "protection":
        source.attack_protection = {
            "turn": state.turn_number + 1,
            "damage": True,
            "effects": True,
        }
    elif kind == "opponent_hand_energy":
        reveal(list(o.hand), state, o, "hand_reveal")
        cards = [c for c in o.hand if isinstance(c, EnergyCard)]
        selected = yield from choice(source, cards, state, rule["count"], rule["count"])
        move_cards(
            selected, (o.id, CardPosition.HAND), (o.id, CardPosition.LEFT), state
        )
        shuffle_cards(o.left, state)
    elif kind == "discard_special_energy":
        if o.active:
            target = o.active[0]
            cards = [
                c
                for c in target.attachment
                if isinstance(c, EnergyCard) and c.energyType == EnergyType.SPECIAL
            ]
            selected = yield from choice(source, cards, state)
            discard_attached(target, selected, o)
    elif kind == "move_opponent_energy":
        if o.active and o.bench:
            target = o.active[0]
            from packages.rules.energy_selection import choose_units
            from types import SimpleNamespace

            if any(isinstance(c, EnergyCard) for c in target.attachment):
                selected = yield from choose_units(
                    target, 1, SimpleNamespace(source=source), state
                )
                recipient = (yield from choice(source, list(o.bench), state))[0]
                transfer(target, recipient, selected)
    elif kind == "switch_collect_energy":
        if source in p.bench and p.active:
            switch_pokemon(p.active[0], source, p)
            holders = {
                id(c): h
                for h in p.bench
                for c in h.attachment
                if isinstance(c, EnergyCard)
            }
            cards = [c for h in p.bench for c in h.attachment if id(c) in holders]
            selected = yield from choice(source, cards, state, 0, len(cards))
            for card in selected:
                transfer(holders[id(card)], source, [card])
    else:
        raise ValueError("Unknown entry effect: " + kind)
    reconcile(state)


def transfer(holder, recipient, cards):
    for card in cards:
        holder.attachment.remove(card)
        recipient.attachment.append(card)
    for pokemon in (holder, recipient):
        pokemon.dynamic_energy = True
        refresh_energy(pokemon)
        for index, card in enumerate(pokemon.attachment):
            card.index = index + 1
            card.cardPosition = (
                CardPosition.ACTIVE_ATTACHMENT
                if pokemon.cardPosition == CardPosition.ACTIVE
                else CardPosition.BENCH_ATTACHMENT
            )


def switch_targets(rule, player):
    return [
        c
        for c in player.bench
        if has_type(c, CardType[rule["type"]]) and c.name != rule.get("exceptName")
    ]

from packages.rules.healing import value as healed

from packages.rules.pokemon_types import has_type

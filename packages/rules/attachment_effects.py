"""Choose physical Energy cards, then their recipients, without consuming manual attachment."""

from ptcg.core.action import choose_card_actions
from ptcg.core.card import EnergyCard
from ptcg.core.enums import CardType, CardPosition, EnergyType, Stage, PokemonRule
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import current_player, move_cards, shuffle_cards
from packages.rules.core_fixes import refresh_energy
from packages.rules.maximum_hp import reconcile


def matches(card, rule):
    return (
        isinstance(card, EnergyCard)
        and (not rule.get("basic") or card.energyType == EnergyType.BASIC)
        and (not rule.get("type") or has_type(card, CardType[rule["type"]]))
        and (not rule.get("types") or any(has_type(card, t) for t in rule["types"]))
        and (not rule.get("name") or card.name == rule["name"])
    )


def recipients(source, rule, p):
    targets = (
        [source]
        if rule.get("target") == "self"
        else list(p.bench if rule.get("target") == "bench" else p.active + p.bench)
    )
    targets = [
        c
        for c in targets
        if (not rule.get("targetType") or has_type(c, CardType[rule["targetType"]]))
        and (not rule.get("targetTypes") or any(has_type(c, t) for t in rule["targetTypes"]))
        and (not rule.get("targetPrefix") or c.name.startswith(rule["targetPrefix"]))
        and (not rule.get("targetName") or c.name == rule["targetName"])
        and (not rule.get("targetStage") or c.stage == Stage[rule["targetStage"]])
        and (
            not rule.get("targetTrait")
            or c.pokemonRule == PokemonRule[rule["targetTrait"]]
        )
    ]
    return targets


def resolve(source, rule, state):
    p = current_player(state)
    if rule.get("countTerm") == "opponent_taken_prizes":
        from ptcg.utils.utils import opponent_player
        rule = {**rule, "count": max(0, 6 - len(opponent_player(state).prize))}
    attached = []
    origin = "left" if rule["origin"] == "top" else rule["origin"]
    pool = list(getattr(p, origin))
    if rule["origin"] == "top":
        pool = pool[: rule["top"]]
    cards = [c for c in pool if matches(c, rule)]
    targets = recipients(source, rule, p)
    if targets and rule.get("distinctTargets"):
        selected_targets = yield from reduce_choose_card_actions(
            choose_card_actions(
                p.id,
                p.id,
                min(len(cards), len(targets)) if rule.get("eachTarget") else 0,
                min(len(cards), len(targets)) if rule.get("eachTarget") else min(rule["count"], len(targets)),
                targets,
                indexed=True,
                source=source,
            ),
            state,
        )
        for target in selected_targets:
            chosen = []
            if cards:
                chosen = yield from reduce_choose_card_actions(
                    choose_card_actions(
                        p.id, p.id, int(origin == "discard"), 1, cards, indexed=True, source=source
                    ),
                    state,
                )
            for card in chosen:
                cards.remove(card)
                move_cards(
                    card,
                    (p.id, CardPosition[origin.upper()]),
                    (
                        p.id,
                        CardPosition.ACTIVE_ATTACHMENT
                        if target in p.active
                        else CardPosition.BENCH_ATTACHMENT,
                        target.index,
                    ),
                    state,
                )
                target.dynamic_energy = True
                refresh_energy(target)
                reconcile(state)
                attached.append(target)
    elif cards and targets:
        count = len(cards) if rule["count"] == "any" else min(rule["count"], len(cards))
        if rule.get("distinctTypes"):
            from packages.rules.entry_effects import choice
            chosen = []
            available = list(cards)
            while available and len(chosen) < count:
                selection = yield from choice(source, available, state, 0, 1)
                if not selection:
                    break
                chosen.extend(selection)
                available = [c for c in available if c.cardType != selection[0].cardType]
        else:
            chosen = yield from reduce_choose_card_actions(
            choose_card_actions(
                p.id,
                p.id,
                count if rule.get("mandatory") else 0,
                count,
                cards,
                indexed=True,
                source=source,
            ),
                state,
            )
        fixed_target = None
        if chosen and rule.get("singleTarget"):
            fixed_target = (
                yield from reduce_choose_card_actions(
                    choose_card_actions(p.id, p.id, 1, 1, targets, source=source), state
                )
            )[0]
        for card in chosen:
            target = fixed_target or (
                targets[0]
                if len(targets) == 1
                else (
                    yield from reduce_choose_card_actions(
                        choose_card_actions(p.id, p.id, 1, 1, targets, source=source),
                        state,
                    )
                )[0]
            )
            destination = (
                CardPosition.ACTIVE_ATTACHMENT
                if target in p.active
                else CardPosition.BENCH_ATTACHMENT
            )
            move_cards(
                card,
                (p.id, CardPosition[origin.upper()]),
                (p.id, destination, target.index),
                state,
            )
            target.dynamic_energy = True
            refresh_energy(target)
            reconcile(state)
            attached.append(target)
    if rule.get("rest") == "shuffle":
        shuffle_cards(p.left,state)
    elif rule.get("rest"):
        rest = [c for c in pool if c in p.left]
        if rule["rest"] == "bottom":
            shuffle_cards(rest, state)
        move_cards(rest, (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD if rule["rest"] == "discard" else CardPosition.LEFT), state)
    elif origin == "left":
        shuffle_cards(p.left, state)
    if attached and rule.get("poisonActive") and p.active[0] in attached:
        p.active[0].poisoned, p.active[0].poison_damage = True, 10
    if attached and (rule.get("thenDraw") or rule.get("thenDrawUntil")):
        count = rule.get("thenDraw", max(0, rule.get("thenDrawUntil", 0) - len(p.hand)))
        move_cards(
            list(p.left[:count]),
            (p.id, CardPosition.LEFT),
            (p.id, CardPosition.HAND),
            state,
        )
    return attached

from packages.rules.pokemon_types import has_type

"""Repeatable or once-per-turn Energy attachment abilities."""
from packages.rules.maximum_hp import maximum


from ptcg.core.card import EnergyCard
from ptcg.core.enums import CardType, EnergyType, CardPosition
from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import current_player, move_cards
from packages.rules.core_fixes import refresh_energy


def candidates(rule, p):
    cards = [
        c
        for c in getattr(p, rule["origin"])
        if isinstance(c, EnergyCard)
        and c.energyType == EnergyType.BASIC
        and has_type(c, CardType[rule["type"]])
    ]
    targets = [
        c
        for c in p.active + p.bench
        if (not rule.get("targetType") or has_type(c, CardType[rule["targetType"]]))
        and (not rule.get("prefix") or c.name.startswith(rule["prefix"]))
        and c.hp > rule.get("counterCost", 0)
    ]
    return cards, targets


def resolve(source, rule, state):
    p = current_player(state)
    cards, targets = candidates(rule, p)
    selected = yield from reduce_choose_card_actions(
        choose_card_actions(p.id, p.id, 1, 1, cards, source=source), state
    )
    target = (
        yield from reduce_choose_card_actions(
            choose_card_actions(p.id, p.id, 1, 1, targets, source=source), state
        )
    )[0]
    zone = (
        CardPosition.ACTIVE_ATTACHMENT
        if target in p.active
        else CardPosition.BENCH_ATTACHMENT
    )
    move_cards(
        selected,
        (p.id, CardPosition[rule["origin"].upper()]),
        (p.id, zone, target.index),
        state,
    )
    target.dynamic_energy = True
    refresh_energy(target)
    from packages.rules.maximum_hp import reconcile

    reconcile(state)
    target.hp -= rule.get("counterCost", 0)
    if rule.get("heal"):
        target.hp = healed(target, rule["heal"], state, record=True)

from packages.rules.healing import value as healed

from packages.rules.pokemon_types import has_type

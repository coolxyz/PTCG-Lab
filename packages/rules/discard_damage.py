"""Pay selected physical cards once and derive attack damage from that payment."""

from ptcg.core.action import choose_card_actions
from ptcg.core.card import EnergyCard, ToolCard
from ptcg.core.enums import EnergyType, CardType, CardPosition
from ptcg.core.reducer import reduce_choose_card_actions, reduce_attack_action
from ptcg.utils.utils import current_player, move_cards
from packages.rules.core_fixes import refresh_energy
from packages.rules.zone_effects import discard_attached


def resolve(rule, action, state):
    p = current_player(state)
    holders = (
        p.bench
        if rule["zone"] == "bench"
        else [action.source]
        if rule["zone"] == "self"
        else p.active + p.bench
    )
    for holder in holders:
        refresh_energy(holder)
    pool = (
        list(p.hand)
        if rule["zone"] == "hand"
        else [c for holder in holders for c in holder.attachment]
    )

    def eligible(c):
        if rule.get("tools"):
            return isinstance(c, ToolCard)
        return (
            isinstance(c, EnergyCard)
            and (not rule.get("basic") or c.energyType == EnergyType.BASIC)
            and (
                not rule.get("type")
                or any(energy_matches(e, rule["type"]) for e in c.provides)
            )
        )

    pool = [c for c in pool if eligible(c)]
    chosen = []
    count = min(rule.get("maxCards", len(pool)), len(pool))
    if rule.get("mandatoryAll"):
        chosen = pool
    elif count:
        chosen = yield from reduce_choose_card_actions(
            choose_card_actions(
                p.id, p.id, 0, count, pool, indexed=True, source=action.source
            ),
            state,
        )
    if rule["zone"] == "hand":
        move_cards(
            chosen, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state
        )
    else:
        for holder in holders:
            discard_attached(holder, [c for c in chosen if c in holder.attachment], p)
    action.attack.damage = (action.attack.damage if rule["mode"] == "add" else 0) + (len({c.cardType for c in chosen}) if rule.get("distinctTypes") else len(chosen)) * rule["factor"]
    yield from reduce_attack_action(action, state)

from packages.rules.energy_units import matches as energy_matches

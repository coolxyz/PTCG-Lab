"""Choose physical cards paying Energy units, allowing multi-unit cards as one.

Official Ampharos ex ruling permits both one Double Turbo and Double Turbo plus
one basic card for its two-Energy discard. Never force a minimum-card payment.
"""

from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.core.card import EnergyCard
from ptcg.core.enums import CardType
from packages.rules.core_fixes import refresh_energy


def choose_units(target, count, action, state, element=None):
    refresh_energy(target)

    def units(card):
        return (
            sum(energy_matches(e, element) for e in card.provides)
            if element
            else len(card.provides)
        )

    cards = [c for c in target.attachment if isinstance(c, EnergyCard) and units(c)]
    required = min(count, sum(units(c) for c in cards))
    chosen = []
    while cards and len(chosen) < required:
        supplied = sum(units(c) for c in chosen)
        selection = yield from reduce_choose_card_actions(
            choose_card_actions(
                state.turn,
                state.turn,
                int(supplied < required),
                1,
                cards,
                indexed=True,
                source=action.source,
                tips=f"Choose Energy cards ({supplied}/{required} units available in selected cards); stop when sufficient.",
            ),
            state,
        )
        if not selection:
            break
        chosen.extend(selection)
        cards.remove(selection[0])
    return chosen


def choose_types(target, elements, action, state):
    """Match all payable typed units without spending one wildcard twice."""
    from itertools import combinations
    from packages.rules.core_fixes import check_energy
    refresh_energy(target, state)
    pool = [c for c in target.attachment if isinstance(c, EnergyCard) and any(energy_matches(e, t) for e in c.provides for t in elements)]
    units = [e for c in pool for e in c.provides]
    costs = [CardType[t] for t in elements]
    payable = next((list(subset) for n in range(len(costs), -1, -1) for subset in combinations(costs, n) if check_energy(subset, units)), [])
    chosen = []
    from itertools import permutations
    def distinct_cards_payable(cards):
        return any(all(any(energy_matches(e, t) for e in c.provides) for c, t in zip(cards, assignment)) for assignment in permutations(payable, len(cards)))
    while pool and len(chosen) < len(payable):
        pool = [c for c in pool if distinct_cards_payable(chosen + [c])]
        if not pool:
            break
        sufficient = check_energy(payable, [e for c in chosen for e in c.provides])
        selected = yield from reduce_choose_card_actions(choose_card_actions(state.turn, state.turn, 0 if sufficient else 1, 1, pool, indexed=True, source=action.source), state)
        if not selected:
            break
        chosen += selected
        pool.remove(selected[0])
    return chosen

from packages.rules.energy_units import matches as energy_matches

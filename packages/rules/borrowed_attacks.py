"""Attacks granted by abilities still pay the actual attacker's energy cost."""

import copy
from ptcg.core.action import AttackAction
from packages.rules.abilities import enabled
from packages.rules.core_fixes import check_energy
from packages.rules.modifiers import effective_attack_cost


def actions(player, state):
    if player.firstTurn and player.id == state.starting_player:
        return []
    opponent = state.player2 if player is state.player1 else state.player1
    if not opponent.active:
        return []
    memory = any(enabled(c, state) and any(r["kind"] == "borrow_evolutions" for r in (getattr(c, "spec", None) or {}).get("abilities", [])) for c in player.active + player.bench)
    result = []
    def previous(c):
        for lower in getattr(c, "evolved", []):
            yield lower
            yield from previous(lower)
    for source in player.active:
        donors = list(previous(source)) if memory else []
        if enabled(source, state) and any(r["kind"] == "borrow_bench" for r in (getattr(source, "spec", None) or {}).get("abilities", [])):
            donors += player.bench
        for donor in donors:
            for attack, printed in zip(donor.attacks, type(donor)().attacks):
                cost = effective_attack_cost(source, attack, printed.cost, state)
                if check_energy(cost, source.energy):
                    action = AttackAction(player.id, source, attack, opponent.active[0])
                    action.attack.cost = cost
                    action.effect_source = donor
                    action.borrowed_attack = True
                    result.append(action)
    return result


def resolve(donor, action, state):
    proxy = copy.copy(donor)
    proxy.energy, proxy.attachment, proxy.position = action.source.energy, action.source.attachment, action.source.position
    yield from proxy.reduce_action(action, state)

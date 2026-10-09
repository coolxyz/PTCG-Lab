"""A second printed attack after all first-attack reactions and replacements."""

from packages.rules.abilities import enabled
from packages.rules.core_fixes import check_energy, refresh_energy
from packages.rules.attack_restrictions import allowed
from ptcg.core.enums import SpecialCondition
from ptcg.core.action import AttackAction
from packages.rules.effects import NumberOption


def candidate(action):
    return isinstance(action, AttackAction) and not getattr(action, "festival_second", False) and not hasattr(action, "effect_source") and any(r["kind"] == "festival_lead" for r in (getattr(action.source, "spec", None) or {}).get("abilities", []))


def second(action, state):
    source = action.source
    p = state.player1 if state.turn == state.player1.id else state.player2
    o = state.player2 if p is state.player1 else state.player1
    if source not in p.active or not o.active or not enabled(source, state) or not any(c.name == "Festival Grounds" for c in state.stadium):
        return
    if getattr(source, "special_condition", None) in (SpecialCondition.PARALYZED, SpecialCondition.ASLEEP):
        return
    refresh_energy(source, state)
    from packages.rules.modifiers import refresh_costs
    refresh_costs(source, state)
    attacks = [a for a in source.attacks if check_energy(a.cost, source.energy) and allowed(source, a, state)]
    if not attacks:
        return
    options = [NumberOption(p, 0, "结束回合")] + [NumberOption(p, i + 1, a.name) for i, a in enumerate(attacks)]
    chosen = yield (state.get_obs(p.id), 0, False, {"raw_available_actions": options})
    if chosen.value:
        result = AttackAction(p.id, source, attacks[chosen.value - 1], o.active[0])
        result.festival_second = True
        return result

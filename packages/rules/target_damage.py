from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions, _calculate_damage
from ptcg.utils.utils import current_player, opponent_player, next_turn
from packages.rules.core_fixes import shield_damage
from packages.rules.knockouts import resolve_group
from packages.rules.attack_math import damage
from packages.rules.zone_effects import resolve as resolve_zone
from packages.rules.modifiers import attack_damage


def resolve(rule, action, state):
    p, o = current_player(state), opponent_player(state)
    candidates = list(o.bench if rule["zone"] == "bench" else o.active + o.bench)
    from ptcg.core.enums import PokemonType
    from packages.rules.attack_math import counters
    candidates = [c for c in candidates if (not rule.get("damaged") or counters(c)>0) and (not rule.get("ex") or c.pokemonType==PokemonType.EX) and (not rule.get("exV") or c.pokemonType in (PokemonType.EX,PokemonType.V,PokemonType.VSTAR))]
    n = min(rule["count"], len(candidates))
    targets = []
    if n:
        targets = yield from reduce_choose_card_actions(
            choose_card_actions(
                p.id, p.id, n, n, candidates, indexed=True, source=action.source
            ),
            state,
        )
        amount = rule["amount"]
        if rule.get("term"):
            amount = damage(
                {"term": rule["term"], "factor": amount, "mode": "multiply"},
                action,
                state,
            )
        for target in targets:
            if rule.get("targetCounters"):
                amount = rule["amount"] * counters(target)
            if rule.get("ignoreEffects") and rule.get("ignoreWeaknessResistance"):
                actual = attack_damage(action.source, target, amount, state)
            else:
                actual = (
                    _calculate_damage(action.source, target, amount, state)
                    if target in o.active and not rule.get("ignoreWeaknessResistance")
                    else shield_damage(
                        target,
                        attack_damage(action.source, target, amount, state),
                        state,
                        action.source,
                    )
                )
            from packages.rules.damage_events import deal
            deal(action.source, target, actual, state)
            p.reward.apply_damage_dealt_reward(actual)
    if rule.get("discardEnergy"):
        yield from resolve_zone(
            {"kind": "discard_self_energy", "count": rule["discardEnergy"], **({"type":rule["discardType"]} if rule.get("discardType") else {})},
            action,
            state,
        )
    yield from resolve_group(state, damage_targets=targets)
    next_turn(state)

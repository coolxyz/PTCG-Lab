"""One RNG result controls damage and all related post-damage effects."""

from ptcg.core.enums import Coin
from ptcg.core.reducer import reduce_attack_damage
from ptcg.utils.utils import flip_coin, next_turn


def resolve(rule, action, state):
    count = rule["flips"]
    heads = 0
    if count == "until_tails":
        while flip_coin(state) == Coin.HEAD:
            heads += 1
    else:
        heads = sum(flip_coin(state) == Coin.HEAD for _ in range(count))
    if rule.get("failZero") and heads == 0:
        next_turn(state)
        return
    if rule.get("bonus"):
        action.attack.damage += rule["bonus"][heads]
    if rule.get("perHead"):
        action.attack.damage = heads * rule["perHead"]

    def after_damage(action, state):
        from packages.rules.plain import PlainPokemon
        effects = []
        if heads >= rule.get('minimumHeads', 1) and (not rule.get("allHeads") or heads == count):
            effects += rule.get("heads", [])
        if heads == 0:
            effects += rule.get("tails", [])
        for name, number in (("perHeadEffect", heads), ("perTailEffect", count - heads if isinstance(count, int) else 1)):
            if number and rule.get(name):
                effect = rule[name]
                effects.append({**effect, "count": effect["count"] * number})
        if rule.get("repeatPerHead"):
            effects += [rule["repeatPerHead"]] * heads
        for effect in effects:
            yield from PlainPokemon.resolve_mechanic(action.source, effect, action, state)

    yield from reduce_attack_damage(action, state, after_damage=after_damage)
    next_turn(state)

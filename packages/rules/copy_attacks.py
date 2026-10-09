"""Copy printed attacks while retaining the physical attacker and declared name."""

import copy
from ptcg.core.card import PokemonCard
from ptcg.core.action import AttackAction
from ptcg.core.enums import CardPosition, Coin, PokemonRule, PokemonType
from ptcg.utils.utils import current_player, opponent_player, flip_coin, move_cards, shuffle_cards, next_turn
from packages.rules.entry_effects import reveal
from packages.rules.trainer_operations import select


def copy_rule(card, attack):
    spec = getattr(card, "spec", None) or {}
    if attack in getattr(card, "attacks", []) and spec.get("attacks"):
        rule = spec["attacks"][card.attacks.index(attack)].get("mechanic", {})
        if rule.get("kind") == "copy_attack":
            return rule
    if attack.name == "Genome Hacking":
        return {"kind": "copy_attack"}
    return None


def resolve(rule, action, state):
    p, o = current_player(state), opponent_player(state)
    source = action.source
    shuffle_after = []
    contexts = set()
    def context(r):
        if r.get("coin") or r.get("zone") == "mill":
            return None
        return (r.get("zone", "opponent_active"), r.get("prefix"), bool(r.get("tera")), bool(r.get("opponentChooses")), r.get("count"))
    # Copying another copy attack is legal. Every iteration exposes a choice
    # instead of growing the Python stack or arbitrarily prohibiting a name.
    while True:
        if rule.get("coin") and flip_coin(state) != Coin.HEAD:
            break
        zone = rule.get("zone", "opponent_active")
        signature = context(rule)
        if signature is not None:
            contexts.add(signature)
        owner = p if zone in ("own_bench", "mill") else o
        if zone == "mill":
            pool = list(p.left[:1])
            move_cards(pool, (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD), state)
            pool = [c for c in pool if isinstance(c, PokemonCard) and c.pokemonType == PokemonType.NORMAL and c.pokemonRule not in (PokemonRule.RADIANT, PokemonRule.TERA)]
        elif zone == "opponent_top":
            pool = list(o.left[:rule["count"]])
            reveal(pool, state, o)
            shuffle_after.append(o)
        else:
            pool = list(p.bench if zone == "own_bench" else o.active + o.bench if zone == "opponent_field" else o.active)
        pool = [c for c in pool if isinstance(c, PokemonCard)
                and (not rule.get("prefix") or c.name.startswith(rule["prefix"]))
                and (not rule.get("tera") or c.pokemonRule == PokemonRule.TERA)]
        pairs = [(c, a) for c in pool for a in c.attacks]
        # Collapse deterministic edges that only revisit an identical choice
        # set. All eventual attack outcomes remain reachable, including copying
        # a different copy effect; a forced copy-only cycle has no effect.
        pairs = [(c, a) for c, a in pairs if not ((nested := copy_rule(c, a)) and context(nested) in contexts)]
        chooser = o if rule.get("opponentChooses") else p
        chosen = yield from select(source, [a for _, a in pairs], state, chooser, 0 if rule.get("optional") else 1, 1)
        if not chosen:
            break
        attack = chosen[0]
        donor = next(c for c, a in pairs if a is attack)
        nested = copy_rule(donor, attack)
        if nested:
            rule = nested
            continue
        proxy = copy.copy(donor)
        proxy.energy, proxy.attachment, proxy.position = source.energy, source.attachment, source.position
        copied = AttackAction(p.id, source, attack, o.active[0])
        copied.declared_name = getattr(action, "declared_name", action.attack.name)
        state.copy_attack_pending = True
        try:
            yield from proxy.reduce_action(copied, state)
        finally:
            state.copy_attack_pending = False
        break
    for owner in shuffle_after:
        shuffle_cards(owner.left, state)
    next_turn(state)

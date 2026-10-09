"""Activated effect availability and costs, shared with the entry primitives."""

from ptcg.core.card import EnergyCard
from ptcg.core.enums import EnergyType, CardType, CardPosition
from ptcg.utils.utils import current_player, opponent_player, move_cards, next_turn


def cost_cards(rule, p):
    return [
        c
        for c in p.hand
        if isinstance(c, EnergyCard)
        and c.energyType == EnergyType.BASIC
        and c.cardType == CardType[rule["discardBasicType"]]
    ]


def available(source, rule, state):
    p, o = current_player(state), opponent_player(state)
    from packages.rules.activated_primitives import KINDS, cost_cards as extra_cost, available as primitive_available
    if rule.get("cost") and not extra_cost(source, rule, p):
        return False
    if rule.get("requiresNames") and not set(rule["requiresNames"]).issubset({c.name for c in p.active + p.bench}):
        return False
    if rule.get("requiresTool") and not any(c.name == rule["requiresTool"] for c in source.attachment):
        return False
    if rule.get("requiresActiveAbility") and not any(a.name == rule["requiresActiveAbility"] for c in p.active for a in getattr(c, "ability", [])):
        return False
    if rule.get("requiresSupporter") and not any(a.get("source") == rule["requiresSupporter"] and a.get("action_type") == "UseSupporterAction" for a in p.current_turn_actions):
        return False
    if rule.get("maxHP") and source.hp > rule["maxHP"]:
        return False
    if rule.get("firstTurnOnly") and not p.firstTurn:
        return False
    if rule.get("benchOnly") and source not in p.bench:
        return False
    if len(o.prize) > rule.get("maxOpponentPrizes", 6):
        return False
    if rule.get("discardBasicType") and not cost_cards(rule, p):
        return False
    effect = rule["effect"]
    kind = effect["kind"]
    if kind in KINDS:
        return primitive_available(source, effect, state)
    if kind == "discard_hand_draw":
        return bool(p.hand or p.left)
    if kind == "counters":
        return bool(o.bench if effect["zone"] == "bench" else o.active + o.bench)
    if kind == "opponent_switch_discard_self":
        return bool(o.active and o.bench)
    if kind == "switch_poison":
        from packages.rules.entry_effects import switch_targets

        return bool(p.active and switch_targets(effect, p))
    if kind == "attach" and not rule.get("endTurn"):
        from packages.rules.attachment_effects import matches, recipients

        if effect["origin"] in ("top", "left"):
            return bool(p.left and recipients(source, effect, p))
        return bool(recipients(source, effect, p)) and any(
            matches(c, effect) for c in getattr(p, effect["origin"])
        )
    if kind == "heal":
        from packages.rules.healing import eligible
        return any(eligible(c,state) and (effect.get("stage") != "evolved" or c.stage.name != "BASIC") for c in (p.active if effect["target"] == "active" else p.active + p.bench))
    return True


def resolve(source, rule, state):
    from packages.rules.entry_effects import effect, choice

    p = current_player(state)
    if rule.get("cost"):
        from packages.rules.activated_primitives import cost_cards as extra_cost
        cards = yield from choice(source, extra_cost(source, rule, p), state)
        if rule["cost"].get("origin") == "attached":
            from packages.rules.zone_effects import discard_attached
            discard_attached(source, cards, p)
        else:
            move_cards(cards, (p.id, CardPosition.HAND), (p.id, CardPosition.LEFT if rule["cost"].get("bottom") else CardPosition.DISCARD), state)
    if rule.get("discardBasicType"):
        cards = yield from choice(source, cost_cards(rule, p), state)
        move_cards(
            cards, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state
        )
    yield from effect(source, rule["effect"], state)
    if rule.get("selfKnockout"):
        source.hp = 0
    if rule.get("discardSelf"):
        from packages.rules.core_fixes import discard_pokemon
        from ptcg.core.reducer import _force_active_replacement
        discard_pokemon(p, source)
        if not p.active and not p.bench:
            # Resolve simultaneous opposing KOs and all win conditions together.
            from packages.rules.knockouts import resolve_group
            yield from resolve_group(state)
        if not p.active and p.bench:
            yield from _force_active_replacement(p, state, p.id)
    from packages.rules.maximum_hp import settle

    yield from settle(state)
    if rule.get("endTurn"):
        next_turn(state)

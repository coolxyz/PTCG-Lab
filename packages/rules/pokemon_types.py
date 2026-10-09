"""A Pokémon can have two types in play; its printed type stays unchanged."""

from ptcg.core.enums import CardType


def types(card, state=None):
    result = {card.cardType}
    state = state or getattr(card, "rules_state", None)
    rules = [r for r in (getattr(card, "spec", None) or {}).get("abilities", []) if r["kind"] == "dual_type"]
    if state is not None and rules:
        from packages.rules.abilities import enabled
        if enabled(card, state):
            for rule in rules:
                if not rule.get("requiresTool") or any(c.name == rule["requiresTool"] for c in card.attachment):
                    result.update(CardType[t] for t in rule["types"])
    return result


def has_type(card, value, state=None):
    return (CardType[value] if isinstance(value, str) else value) in types(card, state)


def resisted(source, target, state):
    return bool(types(source, state) & set(target.resistance))


def weakness_multiplier(target, state):
    from packages.rules.modifiers import rules
    return max([2]+[r.get("weaknessMultiplier", 2) for r in rules(target, state)])


def weakness_resistance(source, target, damage, state):
    from packages.rules.tool_effects import weak_to
    if weak_to(source, target, state):
        damage *= weakness_multiplier(target, state)
    if resisted(source, target, state):
        damage -= (getattr(target, 'spec', None) or {}).get('resistanceAmount', 30)
    return max(0, damage)

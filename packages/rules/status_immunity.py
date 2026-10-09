"""Live abilities cure and prevent their specified Special Conditions."""


def apply_status(card, status):
    card.special_condition = status
    for attr in ("sleep_coins", "confusion_damage"):
        if hasattr(card, attr):
            delattr(card, attr)


def clear(card, state):
    from packages.rules.abilities import enabled
    from ptcg.core.enums import SpecialCondition
    from packages.rules.modifiers import rules
    statuses = {s for r in rules(card, state) for s in r.get("statuses", [])}
    from packages.rules.setup_doll import is_doll
    if is_doll(card) or (getattr(card,'spec',None) or {}).get('mechanic',{}).get('kind')=='fossil':
        statuses.add("all")
    if enabled(card, state):
        statuses.update(s for r in (getattr(card, "spec", None) or {}).get("abilities", []) for s in r.get("statuses", []) if r["kind"] in ("status_immunity", "protection"))
    for attr in ("poisoned", "burned"):
        if "all" in statuses or attr.upper() in statuses:
            if hasattr(card, attr):
                delattr(card, attr)
            if attr == "poisoned" and hasattr(card, "poison_damage"):
                del card.poison_damage
    if "all" in statuses or getattr(card, "special_condition", SpecialCondition.NONE).name in statuses:
        if hasattr(card, "special_condition"):
            del card.special_condition

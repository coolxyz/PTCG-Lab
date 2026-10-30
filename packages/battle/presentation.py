"""Display-only enrichment from saved public observations; no rule execution."""

from packages.collection.domain import CARDS

TOOL_SOURCE_LABEL = "招式来源 · 已附加道具"


def tool_attack_event(event, before):
    """Recover an omitted single-attack TM label without rewriting saved events.

    Older visible frames already identify the public holder, its attached Tool,
    and the declared English attack. Only the exact approved single-attack TM
    name is accepted. No hand, deck, private command or old runtime is loaded.
    """
    if event.get("actionType") != "AttackAction" or not before:
        return event
    if any(g.get("label") == TOOL_SOURCE_LABEL for g in event.get("cardGroups", [])):
        return event
    parts = event.get("sourceLocation", "").split(":")
    if len(parts) != 3 or parts[:2] not in (["self", "active"], ["opponent", "active"]):
        return event
    try:
        index = int(parts[2])
        if index < 0:
            return event
        holder = before[parts[0]]["active"][index]
    except (ValueError, IndexError, KeyError, TypeError):
        return event
    attack = event.get("text", "").rsplit(" · ", 1)[-1]
    if any(a.get("name") == attack for a in holder.get("attacks", [])):
        return event
    name = "Technical Machine: " + attack
    if name not in holder.get("tool", []):
        return event
    candidates = {
        c["engineId"]: c
        for c in CARDS.values()
        if c.get("effectStatus") == "verified"
        and c.get("englishName") == name
        and c.get("subtype") == "宝可梦道具"
    }
    if len(candidates) != 1:
        return event
    engine_id = next(iter(candidates))
    return {
        **event,
        "cardGroups": [
            *event.get("cardGroups", []),
            {
                "label": TOOL_SOURCE_LABEL,
                "cards": [
                    {"id": engine_id, "name": name, "attacks": [{"name": attack}]}
                ],
            },
        ],
    }

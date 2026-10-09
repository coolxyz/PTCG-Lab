"""Resolve turn-end abilities and delayed Supporter effects before checkup."""

from ptcg.core.enums import CardPosition
from ptcg.utils.utils import current_player, move_cards, next_turn
from packages.rules.abilities import enabled
from packages.rules.effects import NumberOption


def jobs(state):
    p = current_player(state)
    result = []
    for card in p.active + p.bench:
        result.extend((card, r) for r in getattr(card, "delayed_attacks", []) if r["turn"] == state.turn_number)
        if enabled(card, state):
            result.extend((card, rule) for rule in (getattr(card, "spec", None) or {}).get("abilities", [])
                          if rule["kind"] == "end_turn" and (not rule.get("activeOnly") or card in p.active))
        from packages.rules.tool_effects import enabled as tools_enabled
        if card in p.active and tools_enabled(state):
            for tool in card.attachment:
                effect = (getattr(tool, "spec", None) or {}).get("mechanic", {}).get("endAttach")
                if effect:
                    result.append((card, {"tool": tool, "name": tool.name, "effect": effect, "optional": True, "activeOnly": True}))
    result.extend((None, r) for r in getattr(p, "end_turn_effects", []) if r["turn"] == state.turn_number)
    return result


def needed(state):
    return getattr(state, "end_phase_stamp", None) != (state.turn_number, state.turn) and bool(jobs(state))


def finish(state):
    if not getattr(state, "pending_end_phase", False):
        return
    del state.pending_end_phase
    state.end_phase_stamp = (state.turn_number, state.turn)
    p = current_player(state)
    queue = jobs(state)
    while queue:
        index = 0
        if len(queue) > 1:
            options = [NumberOption(p, i, (c.name + ": " if c else "") + r.get("name", "回合结束效果"))
                       for i, (c, r) in enumerate(queue)]
            selected = yield (state.get_obs(p.id), 0, False, {"raw_available_actions": options})
            index = selected.value
        card, rule = queue.pop(index)
        if card and not rule.get("delayed") and ((not rule.get("tool") and not enabled(card, state)) or rule.get("activeOnly") and card not in p.active):
            continue
        if rule.get("tool"):
            from packages.rules.tool_effects import enabled as tools_enabled
            if rule["tool"] not in card.attachment or not tools_enabled(state):
                continue
        if rule.get("delayed"):
            if card not in p.active+p.bench or rule not in getattr(card, "delayed_attacks", []):
                continue
            if rule.get("counters"):
                card.hp -= rule["counters"]
            if rule.get("knockout"):
                card.hp = 0
            if rule.get("discard"):
                from packages.rules.core_fixes import discard_pokemon
                discard_pokemon(p, card)
            from packages.rules.knockouts import resolve_group
            yield from resolve_group(state)
            continue
        if rule.get("optional"):
            if rule.get("drawUntil") and (len(p.hand) >= rule["drawUntil"] or not p.left):
                continue
            selected = yield (state.get_obs(p.id), 0, False, {"raw_available_actions": [
                NumberOption(p, 0, "不使用"), NumberOption(p, 1, "使用回合结束特性")]})
            if not selected.value:
                continue
        if rule.get("effect"):
            from packages.rules.entry_effects import effect
            yield from effect(card, rule["effect"], state)
        if rule.get("drawUntil"):
            move_cards(list(p.left[:max(0, rule["drawUntil"]-len(p.hand))]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
        if rule.get("mill"):
            move_cards(list(p.left[:rule["mill"]]), (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD), state)
        if rule.get("discardHandAtLeast") and len(p.hand) >= rule["discardHandAtLeast"]:
            move_cards(list(p.hand), (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
    next_turn(state)


def finish_pending(state):
    yield from finish(state)
    if getattr(state, "pending_checkup", False):
        from packages.rules.checkup import finish as checkup
        yield from checkup(state)

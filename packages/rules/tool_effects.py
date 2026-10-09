"""Shared Tool suppression and turn-end effects."""


def enabled(state):
    from packages.rules.stadiums import rules
    return not any(r.get("disableTools") for r in rules(state))


def weak_to(source, target, state):
    from packages.rules.modifiers import rules
    from packages.rules.pokemon_types import types
    from ptcg.core.enums import CardType
    modifiers = rules(target, state)
    weakness = next(([CardType[r["weaknessType"]]] for r in modifiers if r.get("weaknessType")), target.weakness)
    changed = getattr(target, "weakness_override", {})
    if changed.get('active') or changed.get("until", -1) >= state.turn_number:
        weakness = [CardType[changed["type"]]]
    return bool(types(source, state) & set(weakness)) and not any(r.get("noWeakness") for r in modifiers)


def end_turn(state):
    stamp = (state.turn_number, state.turn)
    if getattr(state, "end_turn_tool_stamp", None) == stamp:
        return
    state.end_turn_tool_stamp = stamp
    from packages.rules.modifiers import rules
    from packages.rules.maximum_hp import maximum
    p = state.player1 if state.turn == state.player1.id else state.player2
    for c in p.active:
        heal = sum(r.get("healEndTurn", 0) for r in rules(c, state))
        c.hp = healed(c, heal, state, record=True)

from packages.rules.healing import value as healed

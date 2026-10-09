"""Public, physical-card history; names alone cannot distinguish two copies."""


def moved(player, old_active):
    state = getattr(player, "rules_state", None)
    if state is not None:
        for card in player.active:
            if card not in old_active:
                card.moved_active_turn = state.turn_number
        if player.active:
            from packages.rules.suppression import enabled
            enabled(player.active[0], state)
        from packages.rules.field_events import moved as trigger
        trigger(player, old_active, state)


def knocked_out(card, owner, state, damage=False):
    events = getattr(owner, "knockout_history", [])
    owner.knockout_history = [e for e in events if e["turn"] >= state.turn_number-2] + [{
        "turn": state.turn_number, "damage": damage, "name": card.name,
        "type": card.cardType.name, "opponentTurn": state.turn != owner.id}]


def attack_used(action, state):
    from ptcg.utils.utils import current_player
    source = action.source
    p = current_player(state)
    entry = {"turn": state.turn_number, "name": action.attack.name,
             "source": source, "trait": source.pokemonRule.name}
    source.attack_history = [e for e in getattr(source, "attack_history", []) if e["turn"] >= state.turn_number-2] + [entry]
    p.attack_history = [e for e in getattr(p, "attack_history", []) if e["turn"] >= state.turn_number-2] + [entry]


def value(rule, source, p, state):
    term = rule["term"]
    if term == "evolved_this_turn":
        return int(getattr(source, "evolved_turn", None) == state.turn_number and
                   (not rule.get("fromName") or getattr(source, "evolved_from_name", None) == rule["fromName"]))
    if term in ("moved_this_turn", "tool_this_turn", "healed_this_turn"):
        attr = {"moved_this_turn": "moved_active_turn", "tool_this_turn": "hand_tool_turn", "healed_this_turn": "healed_turn"}[term]
        return int(getattr(source, attr, None) == state.turn_number)
    if term == "knockout_last_turn":
        return int(any(e["turn"] == state.turn_number-1 and e["damage"] and e["opponentTurn"]
                       and (not rule.get("type") or e["type"] == rule["type"])
                       and (not rule.get("prefix") or e["name"].startswith(rule["prefix"]))
                       for e in getattr(p, "knockout_history", [])))
    if term == "attack_last_turn":
        return int(any(e["turn"] == state.turn_number-2 and e["name"] == rule["name"] for e in getattr(source, "attack_history", [])))
    if term == "other_trait_attack_last_turn":
        return int(any(e["turn"] == state.turn_number-2 and e["source"] is not source and e["trait"] == rule["trait"] for e in getattr(p, "attack_history", [])))
    if term == "damage_last_turn":
        return sum(e["amount"] for e in getattr(source, "damage_history", []) if e["turn"] == state.turn_number-1)
    raise ValueError(term)


TERMS = {"evolved_this_turn", "moved_this_turn", "tool_this_turn", "healed_this_turn",
         "knockout_last_turn", "attack_last_turn", "other_trait_attack_last_turn", "damage_last_turn"}

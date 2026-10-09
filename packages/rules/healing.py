"""Healing prohibitions do not prevent moving counters or changing maximum HP."""

from packages.rules.maximum_hp import maximum


def blocked(card,state):
    from packages.rules.abilities import enabled
    from packages.rules.modifiers import owner_of
    owner=owner_of(card,state)
    for player in (state.player1,state.player2):
        for holder in player.active+player.bench:
            if not enabled(holder,state):continue
            from packages.rules.ability_protection import blocked as protected
            if protected(card,holder,state):continue
            for rule in (getattr(holder,"spec",None) or {}).get("abilities",[]):
                if rule["kind"]!="healing_block":continue
                if rule.get("opponentActive") and (owner is player or owner is None or card not in owner.active):continue
                return True
    return False


def value(card,amount,state, *, record=False):
    if blocked(card,state):return card.hp
    result = maximum(card) if amount is None else min(maximum(card),card.hp+amount)
    if record and result > card.hp:
        card.healed_turn = state.turn_number
    return result


def eligible(card,state):
    return card.hp < maximum(card) and not blocked(card,state)

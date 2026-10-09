"""Special conditions retained by explicit evolution/devolution exceptions."""


def retained_poison(card, state):
    from packages.rules.abilities import enabled
    from packages.rules.modifiers import owner_of
    if not getattr(card, "poisoned", False):
        return None
    owner = owner_of(card, state)
    if owner is None:
        return None
    opponent = state.player2 if owner is state.player1 else state.player1
    if any(enabled(c, state) and any(r["kind"] == "persistent_poison"
           for r in (getattr(c, "spec", None) or {}).get("abilities", []))
           for c in opponent.active + opponent.bench):
        return getattr(card, "poison_damage", 10)
    return None


def restore_poison(card, amount):
    if amount is not None:
        card.poisoned = True
        card.poison_damage = amount


def devolve(card, owner, destination, state):
    """Lift the top physical card; damage and attached cards stay in play."""
    from packages.rules.maximum_hp import maximum
    from packages.rules.core_fixes import refresh_energy
    from ptcg.core.enums import CardPosition
    if not getattr(card, "evolved", []):
        return None
    damage = maximum(card) - card.hp
    poison = retained_poison(card, state)
    lower = card.evolved[-1]
    lower_stack = getattr(lower, "evolved", [])
    attachments = card.attachment
    zone = owner.active if card in owner.active else owner.bench
    position, card_position, index = card.position, card.cardPosition, card.index
    first_turn = card.firstTurnPlayed
    # Earlier evolution cards retain historical object fields while covered.
    # Reconstitute the revealed card so old statuses and attack effects cannot
    # reappear when it is uncovered.
    vars(lower).clear()
    vars(lower).update(vars(type(lower)()))
    lower.evolved, lower.attachment = lower_stack, attachments
    lower.position, lower.cardPosition, lower.index = position, card_position, index
    lower.firstTurnPlayed = True
    lower.evolved_turn = state.turn_number
    lower.hp -= damage
    lower.rules_state = state
    restore_poison(lower, poison)
    zone[zone.index(card)] = lower
    lower.dynamic_energy = True
    refresh_energy(lower)
    vars(card).clear()
    vars(card).update(vars(type(card)()))
    cards = getattr(owner, destination)
    cards.append(card)
    card.cardPosition, card.index = CardPosition[destination.upper()], len(cards)
    return lower

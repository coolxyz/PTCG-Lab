"""Continuous maximum HP changes preserve the number of damage counters.

Rules: official advanced manual v3.4, D-15/D-16 and I-D (knockouts).
https://www.pokemon-card.com/assets/document/advanced_manual.pdf
"""


def maximum(card, state=None):
    if state is not None:
        reconcile(state)
    return getattr(card, "maximum_hp", None) or type(card)().hp


def reconcile(state):
    from packages.rules.setup_doll import normalize
    normalize(state)
    from packages.rules.modifiers import rules, owner_of
    from packages.rules.core_fixes import refresh_energy

    from packages.rules.field_capacity import refresh
    refresh(state)
    cards = [c for p in (state.player1, state.player2) for c in p.active + p.bench]
    for card in cards:
        card.rules_state = state
    for card in cards:
        owner = owner_of(card, state)
        from packages.rules.core_fixes import discard_card
        for energy in list(card.attachment):
            if (getattr(energy, "spec", None) or {}).get("mechanic", {}).get("rocket") and not card.name.startswith("Team Rocket's "):
                card.attachment.remove(energy)
                discard_card(owner, energy)
        refresh_energy(card, state)
        from packages.rules.status_immunity import clear
        clear(card, state)
        from packages.rules.stadiums import rules as stadium_rules
        from ptcg.core.card import EnergyCard
        from packages.rules.stadiums import modifiers as stadium_modifiers
        if any(r.get("statusImmunityWithEnergy") for r in stadium_modifiers(card, state)) and any(isinstance(e, EnergyCard) for e in card.attachment):
            for attr in ("special_condition", "poisoned", "burned", "poison_damage"):
                if hasattr(card, attr):
                    delattr(card, attr)
    # Evaluate the complete board before applying deltas to any holder.
    values = []
    for card in cards:
        owner = owner_of(card, state)
        opponent = state.player2 if owner is state.player1 else state.player1
        extra = sum(
            r.get("hp", 0) + r.get("hpPerOpponentPrize", 0) * (6 - len(opponent.prize))
            for r in rules(card, state)
        )
        values.append((card, type(card)().hp + extra))
    for card, value in values:
        previous = maximum(card)
        if value != previous:
            card.hp += value - previous
            card.maximum_hp = value
    return any(c.hp <= 0 for c in cards)


def affected(state):
    """Route knockout cascades through the shared resolver only when needed."""
    from packages.rules.stadiums import rules as stadium_rules
    return any(r.get("hp") for r in stadium_rules(state)) or any(
        hasattr(c, "maximum_hp")
        or hasattr(c, "delayed_prize_bonus")
        or any(
            r.get("hp") or r.get("hpPerOpponentPrize") or r.get("kind") == "knockout_prizes"
            for r in (getattr(c, "spec", None) or {}).get("abilities", [])
        )
        or any(
            (getattr(a, "spec", None) or {}).get("mechanic", {}).get("hp")
            for a in c.attachment
        )
        for p in (state.player1, state.player2)
        for c in p.active + p.bench
    )


def settle(state):
    from packages.rules.knockouts import resolve_group
    from packages.rules.hand_events import finish as hand_finish
    yield from hand_finish(state)
    from packages.rules.field_events import finish
    yield from finish(state)

    from packages.rules.field_capacity import settle as settle_capacity
    yield from settle_capacity(state)
    if reconcile(state):
        yield from resolve_group(state)

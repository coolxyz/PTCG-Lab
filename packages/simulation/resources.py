"""Visible-information resource planning for the reviewed Make It Rain effect.

This is a scoped A1 repair, not generic text interpretation or a search engine.
Names are never used to classify energy cards.
"""

import math

RAIN = "Make It Rain"


def basic_energy(card):
    return card.get("superType") == "ENERGY" and card.get("energyType") == "BASIC"


def rain_attacker(own):
    return next(
        (
            c
            for c in own.get("active", [])
            if any(a["name"] == RAIN for a in c.get("attacks", []))
        ),
        None,
    )


def rain_damage(energies, attacker, target):
    damage = 50 * energies
    if not damage:
        return 0
    kind = attacker.get("cardType")
    if kind and kind in target.get("weakness", []):
        damage *= 2
    if kind and kind in target.get("resistance", []):
        damage = max(0, damage - 30)
    # This estimate excludes prevention/modifier effects; the engine is authoritative.
    return damage


def energy_goal(own, opponent):
    attacker = rain_attacker(own)
    if not attacker or not opponent.get("active"):
        return 0
    target = opponent["active"][0]
    for count in range(1, 61):
        if rain_damage(count, attacker, target) >= target.get("hp", 999):
            return count
    return math.ceil(max(0, target.get("hp", 999)) / 50)


def resource_bonus(card, own, opponent):
    """Value resources only when an active attacker lacks its visible damage cost."""
    goal = energy_goal(own, opponent)
    hand_count = sum(basic_energy(c) for c in own.get("hand", []))
    if not goal or hand_count >= goal:
        return 0
    if basic_energy(card):
        return 70
    card_id = card.get("id")
    if card_id == "PAR-163" and own.get("deck_count", 0):
        return 80
    if card_id == "PAL-189" and any(basic_energy(c) for c in own.get("discard", [])):
        return 80
    if card_id == "SVI-171" and any(basic_energy(c) for c in own.get("discard", [])):
        return 75
    if card_id == "OBF-186" and own.get("deck_count", 0):
        return 45
    return 0

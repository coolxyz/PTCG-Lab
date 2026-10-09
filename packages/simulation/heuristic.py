"""General visible-card evaluator for the published effects. No engine state."""

from collections import Counter
from packages.simulation.registry import EFFECTS
from packages.simulation.resources import basic_energy, resource_bonus


def tags(card):
    return set(EFFECTS.get(card.get("id"), {}).get("mechanisms", []))


def deficit(cost, energy):
    have = Counter(energy)
    wildcard = have.pop("ANY", 0)
    colored = Counter(t for t in cost if t != "COLORLESS")
    missing = max(0, sum(max(0, n - have[t]) for t, n in colored.items()) - wildcard)
    return max(missing, len(cost) - len(energy), 0)


def attack_potential(card):
    return max(
        (
            a.get("damage", 0) or (100 if "discard_energy_damage" in tags(card) else 0)
            for a in card.get("attacks", [])
        ),
        default=0,
    )


def value(card, own, opponent):
    field = own["active"] + own["bench"]
    names = {c["name"] for c in field}
    abilities = tags(card)
    score = 10.0
    if card.get("superType") == "POKEMON":
        score += min(20, card.get("hp", 0) / 20) + attack_potential(card) / 20
        if set(card.get("evolveFrom", [])) & names:
            score += 65
        elif card.get("stage") not in (None, "BASIC"):
            score -= 15
        if card.get("stage") == "BASIC" and len(field) < 3:
            score += 35
        if card.get("name") in names:
            score -= 15
        if abilities & {"draw", "draw_choice"}:
            score += 15
    if basic_energy(card):
        score += 20
    if abilities & {"search_basic", "search_pokemon"}:
        score += 25 if len(field) < 3 else 5
    if abilities & {"draw", "hand_reset", "search_any"}:
        score += max(0, 7 - len(own["hand"])) * 5
    score += resource_bonus(card, own, opponent)
    score -= 3 * sum(c["name"] == card.get("name") for c in own["hand"])
    return score


def target(option, own):
    cards = own["active"] + own["bench"]
    return next(
        (c for c in cards if c.get("ref") == option.get("targetRef") and c.get("ref")),
        next((c for c in cards if c["name"] == option.get("target")), {}),
    )


def attachment_value(option, own):
    recipient = target(option, own)
    source = next((c for c in own["hand"] if c.get("id") == option.get("sourceId")), {})
    energy = recipient.get("energy", [])
    provides = source.get("provides", [])
    improvement = max(
        (
            deficit(a.get("cost", []), energy)
            - deficit(a.get("cost", []), energy + provides)
            for a in recipient.get("attacks", [])
        ),
        default=0,
    )
    return 30 * improvement + attack_potential(recipient) / 20 - 12 * len(energy)

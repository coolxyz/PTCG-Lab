"""H2 public card-fact interpretation. No engine state or A1 policy imports.

Only listed expressions are estimated. Engine resolution remains authoritative.
"""

import json
from pathlib import Path

_FACTS = json.loads(
    (
        Path(__file__).resolve().parents[3] / "data/cardpool/plain-pokemon.json"
    ).read_text()
)
PUBLIC_RELEASE = json.loads(
    (Path(__file__).resolve().parents[3] / "data/simulation/effects.json").read_text()
)
PRINTED_HP = {
    c["effectKey"]: c.get("semantics", {}).get("hp", 0)
    for c in PUBLIC_RELEASE["effects"]
}

SPECS = {c["effectKey"]: c for c in _FACTS["cards"]}


def counters(card):
    maximum = card.get(
        "maximumHp",
        SPECS.get(card.get("id"), {}).get(
            "hp", PRINTED_HP.get(card.get("id")) or card.get("hp", 0)
        ),
    )
    return max(0, maximum - card.get("hp", maximum)) // 10


def mechanic(card, attack):
    return next(
        (
            a.get("mechanic", {})
            for a in SPECS.get(card.get("id"), {}).get("attacks", [])
            if a["name"] == attack.get("name")
        ),
        {},
    )


def raw_damage(attack, attacker, target, own, other, rule=None):
    rule = mechanic(attacker, attack) if rule is None else rule
    base = attack.get("damage") or 0
    if rule.get("kind") != "damage_expression":
        return base
    values = {
        "self_counters": counters(attacker),
        "opponent_counters": counters(target),
        "self_energy": len(attacker.get("energy", [])),
        "opponent_energy": len(target.get("energy", [])),
        "active_energy": len(attacker.get("energy", []))
        + len(target.get("energy", [])),
        "own_bench": len(own["bench"]),
        "opponent_bench": len(other.get("bench", [])),
        "both_benches": len(own["bench"]) + len(other.get("bench", [])),
        "own_prizes_taken": 6 - own.get("prize_count", 6),
        "opponent_prizes_taken": 6 - other.get("prize_count", 6),
        "own_hand": len(own["hand"]),
        "opponent_hand": other.get("hand_count", 0),
        "self_damaged": int(counters(attacker) > 0),
        "opponent_damaged": int(counters(target) > 0),
    }
    if rule.get("term") not in values:
        return base
    units = values[rule["term"]]
    if "minimum" in rule:
        units = int(units >= rule["minimum"])
    value = units * rule["factor"]
    return max(
        0,
        value
        if rule["mode"] == "multiply"
        else base + (-value if rule["mode"] == "subtract" else value),
    )


def damage(attack, attacker, target, own, other, rule=None):
    result = raw_damage(attack, attacker, target, own, other, rule)
    if attacker.get("cardType") in target.get("weakness", []):
        result *= 2
    if attacker.get("cardType") in target.get("resistance", []):
        result = max(0, result - 30)
    if target.get("attackProtection", {}).get("damage"):
        return 0
    result = max(0, result - target.get("attackDamageReduction", 0))
    if result <= target.get("preventAttackDamageAtMost", -1):
        return 0
    return result

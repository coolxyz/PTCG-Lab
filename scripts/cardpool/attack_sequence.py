"""Compose only independently matched bilingual, unconditional after-damage clauses."""

import re

AFTER = {
    "attack_protection",
    "opponent_attack_lock",
    "drain_damage",
    "move_self_energy",
    "return_self_energy",
    "reveal_opponent_hand",
    "random_discard_hand",
    "named_attack_lock",
    "draw",
    "draw_until",
    "heal_self",
    "discard_self_energy",
    "discard_typed_energy",
    "attack_lock",
    "recoil",
    "special_status",
    "mill_self",
    "mill_opponent",
    "damage_shield",
    "bench_damage",
    "discard_stadium",
    "place_counters",
    "prevent_retreat",
}


def compile_sequence(p, compile_one):
    en = re.split(r"(?<=\.) ", p.get("eeffect", ""))
    cn = re.findall(r"[^。]+。?", p.get("effectZHS", ""))
    if not 2 <= len(en) <= 5 or not 2 <= len(cn) <= 5:
        return None

    def match(i, j, steps):
        if i == len(en) and j == len(cn):
            return steps if len(steps) >= 2 else None
        if len(steps) >= 3:
            return None
        for a in range(i + 1, len(en) + 1):
            for b in range(j + 1, len(cn) + 1):
                # A clause may contain a coin flip plus its conditional result.
                e, c = " ".join(en[i:a]), "".join(cn[j:b])
                base = {**p, "eeffect": e, "effectZHS": c}
                for modifier in (p.get("damageP", ""), ""):
                    rule = compile_one({**base, "damageP": modifier})
                    if not rule or rule["kind"] not in AFTER | {"damage_expression"}:
                        continue
                    if rule["kind"] == "damage_expression" and steps:
                        continue
                    result = match(a, b, steps + [rule])
                    if result:
                        return result
        return None

    steps = match(0, 0, [])
    if not steps:
        return None
    has_expression = steps[0]["kind"] == "damage_expression"
    if p.get("damageP") and not has_expression:
        return None
    return {"kind": "sequence", "steps": steps}

"""Visible attack opportunity and setup planning for H2, independent of A1."""

from packages.battle.h2_v4.visible_math import SPECS, damage, mechanic


def immediate_win(attack, card, own, other):
    """Recognize a supported public win condition; never invent unknown effects.

    Options/candidates are supplied by the engine. Copy donors must be in the
    mechanic's permitted public zone; nested copying is deliberately not inferred.
    """
    if own.get("prize_count") != 1:
        return False
    rule = mechanic(card, attack)
    if rule.get("kind") == "field_operation":
        return rule.get("operation") == "win_one_prize"
    if rule.get("kind") != "copy_attack":
        return False
    # Do not label coin flips, hidden deck copies, opponent-controlled choices,
    # Tera restrictions or future unknown modifiers as guaranteed victories.
    if set(rule) - {"kind", "zone", "prefix"} or rule.get("zone") not in (
        None,
        "opponent_active",
        "own_bench",
    ):
        return False
    donors = (
        own["bench"] if rule.get("zone") == "own_bench" else other.get("active", [])
    )
    return any(
        mechanic(donor, copied).get("kind") == "field_operation"
        and mechanic(donor, copied).get("operation") == "win_one_prize"
        for donor in donors
        if donor.get("name", "").startswith(rule.get("prefix", ""))
        for copied in donor.get("attacks", [])
    )


def estimate(attack, card, own, other):
    target = (other.get("active") or [{}])[0]
    rule = mechanic(card, attack)
    if rule.get("kind") == "copy_attack":
        donors = (
            own["bench"] if rule.get("zone") == "own_bench" else other.get("active", [])
        )
        return max(
            (
                damage(a, card, target, own, other, mechanic(c, a))
                for c in donors
                if c.get("name", "").startswith(rule.get("prefix", ""))
                for a in c.get("attacks", [])
                if mechanic(c, a).get("kind") != "copy_attack"
            ),
            default=0,
        )
    return damage(attack, card, target, own, other)


def attack_value(attack, card, own, other):
    if immediate_win(attack, card, own, other):
        return 10000
    amount = estimate(attack, card, own, other)
    target = (other.get("active") or [{}])[0]
    # Prefer a real KO over preparation. Non-damage effects get explicit utility.
    score = (400 if amount > 0 and amount >= target.get("hp", 9999) else 65) + min(
        amount, 400
    ) / 8
    if attack.get("name") == "Jolting Charge":
        field = own["active"] + own["bench"]
        missing = sum(
            max(
                0,
                min((len(a.get("cost", [])) for a in c.get("attacks", [])), default=0)
                - len(c.get("energy", [])),
            )
            for c in field
            if c.get("name") != "Joltik"
        )
        from packages.battle.h2_v4.policy import deficit

        combat_ready = any(
            estimate(a, c, own, other) >= 80
            and deficit(a.get("cost", []), c.get("energy", [])) == 0
            for c in field
            for a in c.get("attacks", [])
            if c.get("name") != "Joltik"
        )
        score = (
            170 if missing and not combat_ready and own.get("deck_count", 0) > 0 else 20
        )
    return score


def ready_value(card, own, other, deficit):
    if card.get("attackBlocked"):
        return -100
    return max(
        (
            attack_value(a, card, own, other)
            for a in card.get("attacks", [])
            if a.get("name") not in card.get("limitedAttacks", [])
            and deficit(a.get("cost", []), card.get("energy", [])) == 0
        ),
        default=-100,
    )


def development(card, own):
    field = own["active"] + own["bench"]
    names = {c.get("name") for c in field}
    evolution = SPECS.get(card.get("id"), {}).get(
        "evolvesFrom", card.get("evolveFrom", [])
    )
    score = 80 if set(evolution) & names else 0
    name = card.get("name")
    if name == "Joltik" and "Joltik" not in names:
        score += 110 if sum(len(c.get("energy", [])) for c in field) < 3 else 0
    if name == "N's Zorua" and not names & {"N's Zorua", "N's Zoroark ex"}:
        score += 110
    if name == "N's Reshiram" and "N's Reshiram" not in names:
        score += 60 if names & {"N's Zorua", "N's Zoroark ex"} else 0
    return score

"""Public-board prize exchange and Adrena-Brain risk; no hidden-state search."""

from copy import deepcopy
import math
import time

from apps.api.expert_policies import dragapult_legacy as previous
from packages.battle.h2_v4.visible_math import counters, damage, SPECS
from packages.battle.h2_v4.tempo import estimate, immediate_win
from packages.simulation.a2 import legal

PROFILE = previous.PROFILE
VERSION = "h2-dragapult-tempo-risk-v6"


def field(side):
    return side["active"] + side["bench"]


def ability(card, name):
    return not card.get("abilitiesSuppressed") and any(
        a.get("name") == name for a in card.get("abilities", [])
    )


def snow(own, other):
    return 10 * sum(c.get("hp", 0) > 0 and ability(c, "Freezing Shroud") for c in field(own) + field(other))


def tick(card, amount):
    return amount if card.get("abilities") and card.get("name") != "Froslass" else 0


def monkeys(side):
    # abilityUsed is for the present turn; it resets before the opponent replies.
    return sum(c.get("hp", 0) > 0 and ability(c, "Adrena-Brain")
               and any(t in ("DARK", "ANY") for t in c.get("energy", [])) for c in field(side))


def transfer_capacity(side):
    return min(3 * monkeys(side), sum(counters(c) for c in field(side) if c.get("hp", 0) > 0))


def ready_attacks(card):
    if card.get("hp", 0) <= 0 or card.get("attackBlocked"):
        return []
    return [a for a in card.get("attacks", []) if a.get("name") not in card.get("limitedAttacks", [])
            and previous.base.deficit(a.get("cost", []), card.get("energy", [])) == 0]


def reply_prizes(own, other):
    """Risk bound from visible funded attackers, checkup and charged monkeys.

    No opponent hand, hypothetical topdeck, or uncharged monkey is supplied.
    Pooling counter sources is conservative; this is a risk score, not a proof
    that the opponent has a forced win.
    """
    own = deepcopy(own)
    other = deepcopy(other)
    fall = snow(own, other)
    for side in (own, other):
        for card in field(side):
            if card.get("hp", 0) > 0:
                card["hp"] -= tick(card, fall)
    check_prizes = sum(c.get("prize", 1) for c in field(own) if c.get("hp", 0) <= 0)
    victims = [c for c in field(own) if c.get("hp", 0) > 0]
    live = [c for c in field(other) if c.get("hp", 0) > 0]
    active = (other["active"] or [{}])[0]
    can_switch = active.get("hp", 0) <= 0 or (
        not active.get("retreatBlocked")
        and len(active.get("energy", [])) >= len(active.get("retreatCost", []))
    )
    attackers = [c for c in live if c in other["active"] or can_switch]
    n = monkeys(other)
    capacity = transfer_capacity(other)
    # Unknown own-hand-dependent opposing expressions receive no hidden input.
    public_other = {**other, "hand": []}

    def loss(direct, spread, dive=False):
        # Knapsack over recipients: each ability moves <=3 counters to one card.
        dp = {(0, 0, 0): 0}
        for c in victims:
            base = direct if c in own["active"] else spread.get(c.get("ref"), 0)
            needed = max(0, math.ceil((c["hp"] - base) / 10))
            before = list(dp.items())
            for extra in range(min(6, needed) + 1) if dive and c in own["bench"] else (0,):
                transfer = needed - extra
                uses = math.ceil(transfer / 3)
                if transfer > capacity or uses > n:
                    continue
                for (spent, used, allocated), prizes in before:
                    if spent + transfer <= capacity and used + uses <= n and allocated + extra <= 6:
                        key = (spent + transfer, used + uses, allocated + extra)
                        dp[key] = max(dp.get(key, 0), prizes + c.get("prize", 1))
        return check_prizes + max(dp.values())

    best = loss(0, {})
    for attacker in attackers:
        for attack in ready_attacks(attacker):
            target = (own["active"] or [{}])[0]
            # Copy effects use visible donor attacks and the actual attacker's
            # counters. Printed zero damage is not a zero-damage reply.
            amount = estimate(attack, attacker, public_other, own)
            name = attack.get("name")
            spreads = [{}]
            if name == "Shadow Bullet":
                spreads += [{c.get("ref"): 30} for c in own["bench"] if c.get("hp", 0) > 0 and c.get("name") != "Dragapult ex"]
            best = max(best, max(loss(amount, spread, name == "Phantom Dive") for spread in spreads))
    return best



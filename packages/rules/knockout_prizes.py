"""Prize modifiers for any knockout, including checkup and attack effects."""

from ptcg.core.enums import Coin
from ptcg.utils.utils import flip_coin
from packages.rules.abilities import enabled


def prepare(dead, state):
    # Snapshot all abilities before removing any member of a simultaneous group.
    results = {}
    for owner, card in dead:
        other = state.player2 if owner is state.player1 else state.player1
        count = max(0, card.prize + getattr(card, "knockout_bonus_prizes", 0)
                    - getattr(card, "knockout_prize_reduction", 0))
        forbidden = False
        bonus = getattr(card, "delayed_prize_bonus", {})
        if bonus.get("turn") == state.turn_number:
            count += bonus["count"]
        if enabled(card, state):
            for rule in (getattr(card, "spec", None) or {}).get("abilities", []):
                if rule["kind"] != "knockout_prizes" or rule.get("bonus"):
                    continue
                if rule.get("activeOnly") and card not in owner.active:
                    continue
                if rule.get("coin") and flip_coin(state, owner) != Coin.HEAD:
                    continue
                count = 0 if rule.get("none") else max(0, count-rule.get("reduce", 0))
                forbidden = forbidden or rule.get("none", False)
        if card in owner.active:
            seen = set()
            for holder in other.active + other.bench:
                if not enabled(holder, state):
                    continue
                for rule in (getattr(holder, "spec", None) or {}).get("abilities", []):
                    if rule["kind"] != "knockout_prizes" or not rule.get("bonus"):
                        continue
                    key = rule.get("noStack")
                    if key and key in seen:
                        continue
                    seen.add(key)
                    if not rule.get("coin") or flip_coin(state, other) == Coin.HEAD:
                        count += rule["bonus"]
        results[id(card)] = 0 if forbidden else count
    return results

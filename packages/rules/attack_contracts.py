"""Printed attack-use restrictions and conditional printed energy costs."""

from packages.rules.maximum_hp import maximum


def permitted(rule, source, owner, state):
    other = state.player2 if owner is state.player1 else state.player1
    if rule.get("onlySecondFirst") and not (owner.firstTurn and owner.id != state.starting_player):
        return False
    if rule.get("opponentPrizes") is not None and len(other.prize) != rule["opponentPrizes"]:
        return False
    if rule.get("requiresLastAttack") and not any(e["turn"] == state.turn_number-2 and e["name"] == rule["requiresLastAttack"] for e in getattr(source, "attack_history", [])):
        return False
    if rule.get("forbidTeamLastAttack") and any(e["turn"] == state.turn_number-2 and e["name"] == rule["forbidTeamLastAttack"] for e in getattr(owner, "attack_history", [])):
        return False
    return True


def cost(rule, card, owner, printed):
    change = rule.get("costIf")
    if not change:
        return printed
    condition = change["condition"]
    from ptcg.core.enums import SpecialCondition
    matched = (condition == "empty_hand" and not owner.hand or
               condition == "damaged" and card.hp < maximum(card) or
               condition == "tool" and any(c.name == change["name"] for c in card.attachment) or
               condition == "special_condition" and (getattr(card, "special_condition", SpecialCondition.NONE) != SpecialCondition.NONE or getattr(card, "poisoned", False) or getattr(card, "burned", False)))
    if matched:
        from ptcg.core.enums import CardType
        return [CardType[t] for t in change["cost"]]
    return printed

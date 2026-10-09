"""Field abilities restricting cards played from the opponent's hand."""

from ptcg.core.action import UseItemAction, PutStadiumAction, PlayPokemonAction, EvolvePokemonAction
from ptcg.core.card import ToolCard
from packages.rules.abilities import enabled


def allowed(action, player, state):
    from ptcg.core.action import AttachEnergyAction
    from packages.rules.turn_interrupts import attachment_allowed
    from ptcg.core.enums import CardPosition
    if isinstance(action, AttachEnergyAction) and not attachment_allowed(action.target, CardPosition.HAND, state):
        return False
    opponent = state.player2 if player is state.player1 else state.player1
    for holder in opponent.active + opponent.bench:
        if not enabled(holder, state):
            continue
        for rule in (getattr(holder,"spec",None) or {}).get("abilities",[]):
            if rule["kind"] != "hand_lock":
                continue
            if rule.get("activeOnly") and holder not in opponent.active:
                continue
            if rule.get("requiresTool") and not any(isinstance(c,ToolCard) for c in holder.attachment):
                continue
            mode = rule["cards"]
            if mode == "item" and isinstance(action,UseItemAction):
                return False
            if mode == "stadium" and isinstance(action,PutStadiumAction):
                return False
            if mode == "ace" and action.source in player.hand and getattr(action.source,"aceSpec",False):
                return False
            if mode == "ability_pokemon" and isinstance(action,(PlayPokemonAction,EvolvePokemonAction)) and getattr(action.source,"ability",[]) and not action.source.name.startswith("Team Rocket's "):
                return False
    return True

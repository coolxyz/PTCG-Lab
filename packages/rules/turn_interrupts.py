"""Card-text interruptions at the hand-play boundary."""

class EndTurnByAttachment(Exception):
    pass


def trainer_failed(action, state):
    from ptcg.core.action import UseItemAction, UseSupporterAction, UseToolAction, PutStadiumAction
    from ptcg.core.enums import CardPosition, Coin
    from ptcg.utils.utils import current_player, move_cards, flip_coin
    p = current_player(state)
    if (isinstance(action, (UseItemAction, UseSupporterAction, UseToolAction, PutStadiumAction))
            and action.source in p.hand and getattr(p, "trainer_coin_turn", None) == state.turn_number
            and flip_coin(state) == Coin.TAIL):
        move_cards(action.source, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
        return True
    return False


def attachment_allowed(target, source_position, state):
    from ptcg.core.enums import CardPosition
    return not (source_position == CardPosition.HAND and getattr(target, "energy_hand_blocked_turn", None) == state.turn_number)

"""Conservation counts physical cards, not derived energy symbols or deck templates."""
from collections import Counter


def physical_cards(state):
    cards = []
    def visit(card):
        cards.append(card)
        for child in getattr(card, 'attachment', []):
            visit(child)
        for child in getattr(card, 'evolved', []):
            visit(child)
    for player in (state.player1, state.player2):
        for zone in ('hand', 'left', 'prize', 'active', 'bench', 'discard', 'lostZone'):
            for card in getattr(player, zone):
                visit(card)
    for card in state.stadium:
        visit(card)
    for card in getattr(state, 'pending_cards', []):
        visit(card)
    return cards


def check_conservation(state):
    cards = physical_cards(state)
    assert len(cards) == len({id(c) for c in cards}), 'DUPLICATE_PHYSICAL_CARD'
    expected = Counter(c.id for p in (state.player1, state.player2) for c in p.deck)
    assert Counter(c.id for c in cards) == expected, 'CARD_CONSERVATION'

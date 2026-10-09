"""Prize identities stay private unless explicitly turned face up."""

from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.core.enums import CardPosition, PokemonPosition, Coin
from ptcg.utils.utils import flip_coin
from packages.rules.effects import NumberOption


def choose(player, state, count, source=None, candidates=None, optional=False):
    cards = list(player.prize if candidates is None else candidates)
    count = min(count, len(cards))
    if not count:
        return []
    return (yield from reduce_choose_card_actions(choose_card_actions(state.turn, state.turn, 0 if optional else count, count, cards, hidden=True, source=source), state))


def take(player, count, state, source=None):
    count = min(count, len(player.prize))
    if not count:
        return
    cards = yield from reduce_choose_card_actions(choose_card_actions(player.id, player.id, count, count, list(player.prize), hidden=True, source=source), state)
    for c in cards:
        player.prize.remove(c)
    state.pending_cards = getattr(state, "pending_cards", []) + cards
    for i, c in enumerate(player.prize):
        c.index = i + 1
    player.reward.apply_prize_card_reward(len(cards))
    for card in cards:
        use = False
        if (player.prize and player.id == state.turn and not getattr(card, "prize_face_up", False)
                and len(player.bench) < player.benchSize and any(r["kind"] == "lucky_prize" for r in (getattr(card, "spec", None) or {}).get("abilities", []))):
            option = yield (state.get_obs(player.id), 0, False, {"raw_available_actions": [NumberOption(player, 0, "加入手牌"), NumberOption(player, 1, "幸运奖励：放于备战区")]})
            use = bool(option.value)
        state.pending_cards.remove(card)
        if hasattr(card, "prize_face_up"):
            del card.prize_face_up
        zone = player.bench if use else player.hand
        zone.append(card)
        card.cardPosition, card.index = CardPosition.BENCH if use else CardPosition.HAND, len(zone)
        if use:
            card.position, card.firstTurnPlayed = PokemonPosition.BENCH, True
            if flip_coin(state, player) == Coin.HEAD:
                yield from take(player, 1, state, card)

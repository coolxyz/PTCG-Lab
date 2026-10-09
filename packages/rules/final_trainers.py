"""Prize exchanges, Pokemon replacement and modern Rare Candy resolution."""

from functools import lru_cache
from ptcg.core.card import PokemonCard, EnergyCard
from ptcg.core.enums import Stage, PokemonType, CardPosition
from ptcg.core.action import EvolvePokemonAction, choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions, reduce_evolve_pokemon_action
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards
from packages.rules.entry_effects import choice, reveal

OPERATIONS = {"rare_candy", "ogre_mask", "dusk_ball", "super_potion", "prize_ticket", "rocket_robot"}


@lru_cache(maxsize=1)
def evolution_links():
    from packages.rules.plain import SPECS
    links = {}
    for spec in SPECS:
        links.setdefault(spec["name"], set()).update(spec["evolvesFrom"])
    return links


def candy_pairs(p):
    from packages.rules.pokemon_replacement import permitted
    if p.firstTurn:
        return []
    links = evolution_links()
    return [(c, target) for c in p.hand if isinstance(c, PokemonCard) and c.stage == Stage.STAGE_2 and permitted(c)
            for target in p.active + p.bench if target.stage == Stage.BASIC and not target.firstTurnPlayed
            and any(target.name in links.get(previous, set()) for previous in c.evolveFrom[:1])]


def ogerpon(c):
    return isinstance(c, PokemonCard) and c.pokemonType == PokemonType.EX and "Ogerpon" in c.name


def playable(op, p, o):
    if op == "rare_candy":
        return bool(candy_pairs(p))
    if op == "ogre_mask":
        return any(ogerpon(c) for c in p.discard) and any(ogerpon(c) for c in p.active + p.bench)
    if op == "dusk_ball":
        return bool(p.left)
    if op == "super_potion":
        from packages.rules.healing import eligible
        return any(eligible(c, p.rules_state) for c in p.active + p.bench)
    if op == "prize_ticket":
        return bool(p.prize)
    if op == "rocket_robot":
        return bool(o.hand) and any(not getattr(c, "prize_face_up", False) for c in o.prize)
    raise ValueError(op)


def resolve(source, r, state):
    p, o = current_player(state), opponent_player(state)
    op = r["op"]
    if op == "rare_candy":
        pairs = candy_pairs(p)
        if not pairs:
            return
        card = (yield from choice(source, list(dict.fromkeys(c for c, _ in pairs)), state))[0]
        target = (yield from choice(source, [t for c, t in pairs if c is card], state))[0]
        reduce_evolve_pokemon_action(EvolvePokemonAction(p.id, card, target), state)
        from packages.rules.entry_effects import resolve as entered
        from packages.rules.hand_events import capture
        capture(card, p, state, "evolution")
        yield from entered(card, "evolve", state)
    elif op == "ogre_mask":
        if not playable(op, p, o):
            return
        card = (yield from choice(source, [c for c in p.discard if ogerpon(c)], state))[0]
        target = (yield from choice(source, [c for c in p.active + p.bench if ogerpon(c)], state))[0]
        from packages.rules.pokemon_replacement import replace
        replace(target, card, p, state, origin="discard", destination="discard")
    elif op == "dusk_ball":
        window = list(p.left[-7:])
        if window:
            yield from choice(source, window, state, len(window), len(window))
        cards = yield from choice(source, [c for c in window if isinstance(c, PokemonCard)], state, 0, 1)
        reveal(cards, state, p)
        move_cards(cards, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
        shuffle_cards(p.left, state)
    elif op == "super_potion":
        from packages.rules.healing import eligible, value
        targets = [c for c in p.active + p.bench if eligible(c, state)]
        if not targets:
            return
        target = (yield from choice(source, targets, state))[0]
        old = target.hp
        target.hp = value(target, 60, state, record=True)
        if target.hp > old:
            from types import SimpleNamespace
            from packages.rules.energy_selection import choose_units
            from packages.rules.zone_effects import discard_attached
            cards = yield from choose_units(target, 1, SimpleNamespace(source=source), state)
            discard_attached(target, cards, p)
    elif op == "prize_ticket":
        cards = list(p.prize)
        shuffle_cards(cards, state)
        for c in cards:
            c.prize_face_up = False
        move_cards(cards, (p.id, CardPosition.PRIZE), (p.id, CardPosition.LEFT), state)
        fresh = list(p.left[:len(cards)])
        move_cards(fresh, (p.id, CardPosition.LEFT), (p.id, CardPosition.PRIZE), state)
    elif op == "rocket_robot":
        from packages.rules.prizes import choose
        from packages.rules.effects import NumberOption
        cards = yield from choose(o, state, 1, source, [c for c in o.prize if not getattr(c, "prize_face_up", False)])
        if not cards or not o.hand:
            return
        prize = cards[0]
        prize.prize_face_up = True
        # A hidden, shuffled physical hand choice does not disclose its identity.
        hidden = list(o.hand)
        state.rng.shuffle(hidden)
        picked = yield from reduce_choose_card_actions(choose_card_actions(p.id, p.id, 1, 1, hidden, hidden=True, source=source), state)
        hand = picked[0]
        reveal([prize, hand], state, o)
        option = yield (state.get_obs(p.id), 0, False, {"raw_available_actions": [NumberOption(p, 0, "保持原位"), NumberOption(p, 1, "交换奖赏卡与手牌")]})
        if option.value:
            pi, hi = o.prize.index(prize), o.hand.index(hand)
            o.prize[pi], o.hand[hi] = hand, prize
            hand.cardPosition, hand.index, hand.prize_face_up = CardPosition.PRIZE, pi + 1, True
            prize.cardPosition, prize.index, prize.prize_face_up = CardPosition.HAND, hi + 1, False

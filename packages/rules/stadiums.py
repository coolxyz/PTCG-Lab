"""Shared Stadium lifecycle, per-turn actions and board-derived modifiers."""

from ptcg.core.action import (
    PutStadiumAction,
    DiscardStadiumAction,
    UseStadiumAction,
    choose_card_actions,
)
from ptcg.core.card import StadiumCard, PokemonCard
from ptcg.core.enums import CardPosition, CardType, Coin
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import (
    current_player,
    move_cards,
    shuffle_cards,
    flip_coin,
    discard_card,
)


def rules(state):
    return [c.spec["mechanic"] for c in state.stadium if isinstance(c, CompiledStadium)]


def used(card, state):
    return getattr(card, "stadium_use_stamp", None) == (state.turn_number, state.turn)


def mark_used(card, state):
    card.stadium_use_stamp = (state.turn_number, state.turn)
    current_player(state).stadiumUsedTurn = True


def applies(r, card):
    return (
        (not r.get("stage") or card.stage.name == r["stage"])
        and (not r.get("type") or has_type(card, r["type"]))
        and (not r.get("exceptType") or not has_type(card, r["exceptType"]))
        and (not r.get("prefix") or card.name.startswith(r["prefix"]))
        and (not r.get('name') or card.name == r['name'])
    )


def modifiers(card, state):
    from packages.rules.zone_guards import stadium_immune
    if stadium_immune(card, state):
        return []
    return [r for r in rules(state) if applies(r, card)]


def matches(card, category):
    from packages.rules.trainers import matches as trainer_matches

    if category == "basic_no_rule":
        from ptcg.core.enums import Stage
        return trainer_matches(card, "no_rule") and card.stage == Stage.BASIC and not getattr(card, "isRadiant", False)

    if category == "marnie":
        return isinstance(card, PokemonCard) and card.name.startswith("Marnie's ")
    if category == "lightning_energy":
        return (
            trainer_matches(card, "basic_energy")
            and has_type(card, CardType.LIGHTNING)
        )
    return trainer_matches(card, category)


class CompiledStadium(StadiumCard):
    spec = None

    def __init__(self):
        super().__init__()
        self.id = self.spec["effectKey"]
        self.set_name, self.number = self.id.split("-", 1)
        self.name, self.text = self.spec["name"], self.spec["text"]
        self.cardType = CardType.NONE
        self.aceSpec = self.spec.get("aceSpec", False)
        self.playedFrom = None

    def costs(self, player):
        r = self.spec["mechanic"]
        return [
            c
            for c in player.hand
            if matches(c, "basic_energy")
            and (r["costType"] == "any" or c.cardType.name == r["costType"])
        ]

    def get_actions(self, state):
        p, r = current_player(state), self.spec["mechanic"]
        result = []
        if (
            self in p.hand
            and not p.stadiumPlayedTurn
            and not any(c.name == self.name for c in state.stadium)
        ):
            result.append(PutStadiumAction(p.id, self))
        if self not in state.stadium or not r.get("use") or used(self, state):
            return result
        use = r["use"]
        if use == 'great_tree':
            from ptcg.core.enums import Stage
            if p.firstTurn or not p.left or not any(c.stage==Stage.BASIC and not c.firstTurnPlayed for c in p.active+p.bench):return result
        if r.get("requiresSupporter") and not p.supporterPlayedTurn:
            return result
        if r.get("requiresRocketSupporter") and getattr(p, "rocket_supporter_turn", None) != state.turn_number:
            return result
        if r.get("costType") and not self.costs(p):
            return result
        if use in ("search", "search_bench", "draw") and not p.left:
            return result
        if use == "search_bench" and len(p.bench) >= p.benchSize:
            return result
        if use == "recover" and not any(matches(c, r["filter"]) for c in p.discard):
            return result
        if use == "topdeck" and not p.hand:
            return result
        if use == "heal":
            from packages.rules.maximum_hp import maximum

            if not any(c.hp < maximum(c) for c in p.active + p.bench):
                return result
        return result + [UseStadiumAction(p.id, self)]

    def reduce_action(self, action, state):
        p, r = current_player(state), self.spec["mechanic"]
        if isinstance(action, DiscardStadiumAction):
            owner = (
                state.player1 if self.playedFrom == state.player1.id else state.player2
            )
            state.stadium.remove(self)
            discard_card(owner, self)
            return
        if isinstance(action, PutStadiumAction):
            for old in list(state.stadium):
                yield from old.reduce_action(
                    DiscardStadiumAction(old.playedFrom, old), state
                )
            self.playedFrom = p.id
            p.stadiumPlayedTurn = True
            move_cards(
                self, (p.id, CardPosition.HAND), (None, CardPosition.STADIUM), state
            )
            from packages.rules.maximum_hp import settle

            yield from settle(state)
            return
        if not isinstance(action, UseStadiumAction):
            raise ValueError(type(action).__name__)
        mark_used(self, state)
        if r.get("costType"):
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(p.id, p.id, 1, 1, self.costs(p), source=self), state
            )
            move_cards(
                chosen, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state
            )
        use = r["use"]
        if use == 'great_tree':
            from packages.rules.expanded_trainers import great_tree
            yield from great_tree(self,state)
        elif use == "search_bench":
            pool = [c for c in p.left if matches(c, r["filter"])]
            if pool:
                chosen = yield from reduce_choose_card_actions(choose_card_actions(p.id, p.id, 0, 1, pool, source=self), state)
                move_cards(chosen, (p.id, CardPosition.LEFT), (p.id, CardPosition.BENCH), state)
                for c in chosen:
                    from ptcg.core.enums import PokemonPosition
                    c.position, c.firstTurnPlayed = PokemonPosition.BENCH, True
            shuffle_cards(p.left, state)
        elif use in ("search", "recover", "topdeck"):
            if r.get("coin") and flip_coin(state) != Coin.HEAD:
                return
            origin = {
                "search": CardPosition.LEFT,
                "recover": CardPosition.DISCARD,
                "topdeck": CardPosition.HAND,
            }[use]
            pool = list(
                {"search": p.left, "recover": p.discard, "topdeck": p.hand}[use]
            )
            if use != "topdeck":
                pool = [c for c in pool if matches(c, r["filter"])]
            chosen = []
            if pool:
                count = min(r.get("count", 1), len(pool))
                chosen = yield from reduce_choose_card_actions(
                    choose_card_actions(
                        p.id,
                        p.id,
                        1 if use == "topdeck" else 0,
                        count,
                        pool,
                        source=self,
                    ),
                    state,
                )
                move_cards(
                    chosen,
                    (p.id, origin),
                    (
                        p.id,
                        CardPosition.LEFT if use == "topdeck" else CardPosition.HAND,
                    ),
                    state,
                )
                if use == "topdeck":
                    for c in reversed(chosen):
                        p.left.remove(c)
                        p.left.insert(0, c)
                    for index, c in enumerate(p.left, 1):
                        c.index = index
                else:
                    from packages.rules.trainer_effects import reveal

                    reveal(p, chosen, state)
            if use == "search":
                shuffle_cards(p.left, state)
        elif use == "draw":
            move_cards(
                list(p.left[: r["draw"]]),
                (p.id, CardPosition.LEFT),
                (p.id, CardPosition.HAND),
                state,
            )
        elif use == "heal":
            from packages.rules.maximum_hp import maximum

            for c in p.active + p.bench:
                from packages.rules.zone_guards import stadium_immune
                if stadium_immune(c, state):
                    continue
                c.hp = healed(c, r["heal"], state, record=True)

from packages.rules.healing import value as healed

from packages.rules.pokemon_types import has_type

"""Shared Item/Supporter actions with explicit playability and hidden searches."""
from packages.rules.maximum_hp import maximum


from ptcg.core.action import (
    UseItemAction,
    UseSupporterAction,
    UseToolAction,
    choose_card_actions,
)
from ptcg.core.card import (
    PokemonCard,
    EnergyCard,
    ItemCard,
    SupporterCard,
    ToolCard,
    StadiumCard,
)
from ptcg.core.enums import (
    CardType,
    CardPosition,
    Stage,
    EnergyType,
    PokemonRule,
    PokemonType,
    Coin,
)
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import (
    current_player,
    opponent_player,
    move_cards,
    shuffle_cards,
    switch_pokemon,
    next_turn,
    flip_coin,
    can_attach_tool,
)


def draw_limit(rule, p, o):
    condition = rule.get("condition")
    bonus = (
        (
            condition == "opponent_ex"
            and bool(o.active)
            and o.active[0].pokemonType == PokemonType.EX
        )
        or (
            condition == "no_attached_energy"
            and not any(
                isinstance(e, EnergyCard)
                for c in p.active + p.bench
                for e in c.attachment
            )
        )
        or (condition == "opponent_three_prizes" and len(o.prize) <= 3)
        or (condition == "all_rocket" and bool(p.active + p.bench) and all(c.name.startswith("Team Rocket's ") for c in p.active + p.bench))
        or (condition == "six_prizes" and len(p.prize) == 6)
    )
    count = rule.get("count", 0)
    if rule.get("countTerm") == "ancient":
        count = sum(c.pokemonRule == PokemonRule.ANCIENT for c in p.active + p.bench)
    if rule.get("countTerm") == "opponent_bench":
        count = len(o.bench)
    return (count if isinstance(count, int) else 0) + (
        rule.get("bonus", 0) if bonus else 0
    )


def matches(card, category):
    if category == "rocket_supporter":
        return isinstance(card, SupporterCard) and "Team Rocket" in card.name
    if category == "trainer":
        return isinstance(card, (ItemCard, ToolCard, SupporterCard, StadiumCard))
    if category.startswith("pokemon_energy:"):
        return (
            matches(card, "pokemon_or_energy")
            and has_type(card, CardType[category.split(":", 1)[1]])
        )
    if category == "ex":
        return isinstance(card, PokemonCard) and card.pokemonType == PokemonType.EX
    if category == "technical_machine":
        return isinstance(card, ToolCard) and "Technical Machine" in card.name
    if category == "hop_basic":
        return matches(card, "basic_pokemon") and card.name.startswith("Hop's ")
    if category == "rocket_basic":
        return matches(card, "basic_pokemon") and card.name.startswith("Team Rocket's ")
    if category == "ethan_or_fire":
        return (isinstance(card, PokemonCard) and card.name.startswith("Ethan's ")) or (
            matches(card, "basic_energy") and has_type(card, CardType.FIRE)
        )
    if category == "no_rule_or_energy":
        return matches(card,"no_rule") or matches(card,"basic_energy")
    if category == "any":
        return True
    if category.startswith("name:"):
        return card.name == category[5:]
    if category == "stadium":
        return isinstance(card, StadiumCard)
    if category == "energy":
        return isinstance(card, EnergyCard)
    if category == "pokemon_or_energy":
        return matches(card, "pokemon") or matches(card, "basic_energy")
    if category == "stage1":
        return isinstance(card, PokemonCard) and card.stage == Stage.STAGE_1
    if category == "future":
        return isinstance(card, PokemonCard) and card.pokemonRule == PokemonRule.FUTURE
    if category == "tera":
        return isinstance(card, PokemonCard) and card.pokemonRule == PokemonRule.TERA
    if category == "basic_120":
        return matches(card, "basic_pokemon") and card.hp <= 120
    if category == "no_rule":
        return (
            isinstance(card, PokemonCard)
            and card.pokemonType == PokemonType.NORMAL
            and card.pokemonRule not in (PokemonRule.TERA, PokemonRule.RADIANT)
        )
    if category == "supporter":
        return isinstance(card, SupporterCard)
    if category == "item":
        return isinstance(card, ItemCard)
    if category == "tool":
        return isinstance(card, ToolCard)
    if category == "pokemon":
        return isinstance(card, PokemonCard)
    if category == "evolution":
        return isinstance(card, PokemonCard) and card.stage != Stage.BASIC
    if category == "basic_pokemon":
        return isinstance(card, PokemonCard) and card.stage == Stage.BASIC
    if category == "basic_energy":
        return isinstance(card, EnergyCard) and card.energyType == EnergyType.BASIC
    raise ValueError(category)


def heal_targets(rule, player, state):
    from packages.rules.healing import eligible
    pool = (
        player.active
        if rule.get("target") == "active"
        else player.active + player.bench
    )
    return [
        c
        for c in pool
        if eligible(c,state)
        and (not rule.get("type") or has_type(c, CardType[rule["type"]]))
        and (not rule.get("maxRemainingHP") or c.hp <= rule["maxRemainingHP"])
    ]


class CompiledTrainer:
    spec = None

    def __init__(self):
        super().__init__()
        self.id = self.spec["effectKey"]
        self.set_name = "P4T"
        self.number = self.id.split("-", 1)[1]
        self.name = self.spec["name"]
        self.text = self.spec["text"]
        self.cardType = CardType.NONE
        self.aceSpec = self.spec.get("aceSpec", False)
        self.pokemonRule = PokemonRule[self.spec.get("trait") or "NONE"]
        from packages.rules.technical_machines import initialize
        initialize(self)

    def get_actions(self, state):
        p = current_player(state)
        r = self.spec["mechanic"]
        kind = r["kind"]
        if r.get('secondFirstTurn') and not (p.firstTurn and p.id != state.starting_player):
            return []
        if kind == "setup_doll":
            return []
        if kind == 'fossil':
            return [UseItemAction(p.id,self)] if self in p.hand and len(p.bench)<p.benchSize else []
        if self not in p.hand:
            if r.get("grantedAttack"):
                from packages.rules.technical_machines import actions
                return actions(self, state)
            return []
        from packages.rules.trainer_effects import playable

        if not playable(r, p, opponent_player(state)):
            return []
        if r.get("requiresTrait") and not any(c.pokemonRule.name == r["requiresTrait"] for c in p.active+p.bench):
            return []
        opponent = opponent_player(state)
        if kind == "trainer_operation":
            if r["op"] == "strip" and r.get("all") and not state.stadium and not any(__import__("packages.rules.trainer_operations",fromlist=["removable"]).removable(e) for c in opponent.active+opponent.bench for e in c.attachment):
                return []
            if r["op"] == "sandwich" and not heal_targets({"target":"active"},p,state):
                return []
        if r.get("requiresMorePrizes") and len(p.prize) <= len(opponent.prize):
            return []
        if r.get("requiresOpponentPoison") and not (opponent.active and getattr(opponent.active[0], "poisoned", False)):
            return []
        if r.get("countTerm") and draw_limit(r, p, opponent_player(state)) == 0:
            return []
        if self.spec["trainerType"] == "tool":
            return [
                UseToolAction(p.id, self, c)
                for c in p.active + p.bench
                if can_attach_tool(c)
            ]
        if len(p.hand) - 1 < r.get("discardCost", 0):
            return []
        if r.get("lastHand") and len(p.hand) != 1:
            return []
        if r.get("afterRocketKnockout") and not any(e["turn"] == state.turn_number-1 and e["opponentTurn"] and e["name"].startswith("Team Rocket's ") for e in getattr(p, "knockout_history", [])):
            return []
        if r.get("afterKnockout") and not p.hasPokemonDead:
            return []
        if self.spec["trainerType"] == "supporter" and not getattr(self, "copied_effect", False):
            first = p.firstTurn and p.id == state.starting_player
            if first:
                if not r.get("allowFirstTurn") or getattr(
                    p, "first_turn_supporter_used", False
                ):
                    return []
            elif p.supporterPlayedTurn:
                return []
        if kind == "legacy_trainer":
            if getattr(self, "copied_effect", False):
                from packages.rules.zone_guards import hand_return_forbidden
                if r["handler"] == "Ciphermaniac" and not p.left:
                    return []
                return [UseSupporterAction(p.id, self)] if r["handler"] != "Turo" or not hand_return_forbidden(p, state) else []
            return legacy_handler(r).get_actions(self, state)
        if (
            kind in ("draw", "discard_draw", "draw_until", "search_hand", "search_pair")
            and not p.left
        ):
            return []
        if kind == "draw_until" and len(p.hand) - 1 - r.get(
            "discardCost", 0
        ) >= draw_limit(r, p, opponent_player(state)):
            return []
        if kind == "shuffle_draw" and not (p.left or len(p.hand) > 1):
            return []
        if kind == "switch" and not p.bench:
            return []
        if kind == "gust" and not opponent_player(state).bench:
            return []
        if kind in ("heal", "heal_own_all") and not heal_targets(r, p, state):
            return []
        if kind == "heal_all" and not any(
            healed(c,1,state) > c.hp
            for player in (p, opponent_player(state))
            for c in player.active + player.bench
        ):
            return []
        if kind == "search_bench" and (not p.left or len(p.bench) >= p.benchSize):
            return []
        if kind in ("recover_deck", "recover_hand") and not any(
            matches(c, r["filter"]) for c in p.discard
        ):
            return []
        o = opponent_player(state)
        if kind == "both_shuffle_draw" and not (
            len(p.hand) > 1 or p.left or o.hand or o.left
        ):
            return []
        cls = (
            UseSupporterAction
            if self.spec["trainerType"] == "supporter"
            else UseItemAction
        )
        return [cls(p.id, self)]

    def reduce_action(self, action, state):
        from ptcg.core.action import AttackAction
        if isinstance(action, AttackAction) and self.spec["mechanic"].get("grantedAttack"):
            from packages.rules.technical_machines import resolve
            yield from resolve(self, action, state)
            return
        p = current_player(state)
        r = self.spec["mechanic"]
        kind = r["kind"]
        if kind == 'fossil':
            from packages.rules.setup_doll import enter
            p.hand.remove(self)
            enter(self)
            from ptcg.core.enums import PokemonPosition
            self.cardPosition,self.position,self.index,self.firstTurnPlayed=CardPosition.BENCH,PokemonPosition.BENCH,len(p.bench)+1,True
            p.bench.append(self)
            for i,c in enumerate(p.hand):c.index=i+1
            from packages.rules.maximum_hp import settle
            yield from settle(state)
            return
        if kind == "legacy_trainer":
            result = legacy_handler(r).reduce_action(self, action, state)
            if result is not None:
                yield from result
            return
        if isinstance(action, UseToolAction) and self.spec["trainerType"] == "tool":
            target = action.target
            destination = (
                CardPosition.ACTIVE_ATTACHMENT
                if target in p.active
                else CardPosition.BENCH_ATTACHMENT
            )
            move_cards(
                self,
                (p.id, CardPosition.HAND),
                (p.id, destination, target.index),
                state,
            )
            self.hasAttached = True
            self.attachedTo = [target]
            from packages.rules.maximum_hp import reconcile

            reconcile(state)
            return
        if not isinstance(action, (UseItemAction, UseSupporterAction)):
            return
        move_cards(self, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
        if isinstance(action, UseSupporterAction):
            p.supporterPlayedTurn = True
            if p.firstTurn:
                p.first_turn_supporter_used = True
        yield from self.resolve_effects(state)

    def resolve_effects(self, state):
        p = current_player(state)
        r = self.spec["mechanic"]
        kind = r["kind"]
        if r.get("coinHeads") and flip_coin(state) != Coin.HEAD:
            return
        original_discard = list(p.discard)
        if r.get("discardCost"):
            n = r["discardCost"]
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(p.id, p.id, n, n, list(p.hand), source=self), state
            )
            move_cards(
                chosen, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state
            )
        draw_count = draw_limit(r, p, opponent_player(state))
        from packages.rules.trainer_field_effects import KINDS as FIELD_KINDS, resolve as resolve_field
        if kind in FIELD_KINDS:
            yield from resolve_field(self, r, state)
            return
        from packages.rules.trainer_effects import (
            KINDS as EXTRA_KINDS,
            resolve as resolve_extra,
        )

        if kind in EXTRA_KINDS:
            yield from resolve_extra(self, r, state)
            return
        if r.get("handCount"):
            draw_count += len(p.hand)
        if kind == "discard_draw":
            move_cards(
                list(p.hand),
                (p.id, CardPosition.HAND),
                (p.id, CardPosition.DISCARD),
                state,
            )
        if kind in ("both_shuffle_draw", "shuffle_draw"):
            for player in (
                (p, opponent_player(state)) if kind == "both_shuffle_draw" else (p,)
            ):
                move_cards(
                    list(player.hand),
                    (player.id, CardPosition.HAND),
                    (player.id, CardPosition.LEFT),
                    state,
                )
                shuffle_cards(player.left, state)
        if kind in (
            "draw",
            "discard_draw",
            "both_shuffle_draw",
            "shuffle_draw",
            "draw_until",
        ):
            if r.get("coinBonus") and flip_coin(state) == Coin.HEAD:
                draw_count += r["coinBonus"]
            for player in (
                (p, opponent_player(state)) if kind == "both_shuffle_draw" else (p,)
            ):
                n = (
                    max(0, draw_count - len(p.hand))
                    if kind == "draw_until"
                    else draw_count
                )
                if player is not p and "opponentCount" in r:
                    n = r["opponentCount"]
                move_cards(
                    list(player.left[:n]),
                    (player.id, CardPosition.LEFT),
                    (player.id, CardPosition.HAND),
                    state,
                )
        elif kind == "heal_own_all":
            for c in heal_targets(r, p, state):
                c.hp = healed(c, r["count"], state, record=True)
        elif kind == "heal_all":
            for player in (p, opponent_player(state)):
                for c in player.active + player.bench:
                    c.hp = healed(c, r["count"], state, record=True)
        elif kind in ("switch", "heal", "gust"):
            owner = opponent_player(state) if kind == "gust" else p
            cards = (
                list(owner.bench) if kind in ("switch", "gust") else heal_targets(r, p, state)
            )
            chosen = yield from reduce_choose_card_actions(
                choose_card_actions(
                    p.id,
                    p.id,
                    1,
                    min(r.get("targets", 1), len(cards)),
                    cards,
                    source=self,
                ),
                state,
            )
            if kind in ("switch", "gust"):
                switch_pokemon(owner.active[0], chosen[0], owner)
            else:
                for c in chosen:
                    c.hp = (
                        healed(c, None, state, record=True)
                        if r["count"] == "all"
                        else healed(c, r["count"], state, record=True)
                    )
            if r.get("thenDrawUntil"):
                n = max(0, r["thenDrawUntil"] - len(p.hand))
                move_cards(
                    list(p.left[:n]),
                    (p.id, CardPosition.LEFT),
                    (p.id, CardPosition.HAND),
                    state,
                )
        elif kind in (
            "search_hand",
            "search_bench",
            "search_pair",
            "recover_deck",
            "recover_hand",
        ):
            recovering = kind.startswith("recover_")
            origin = CardPosition.DISCARD if recovering else CardPosition.LEFT
            destination = (
                CardPosition.LEFT
                if kind == "recover_deck"
                else (
                    CardPosition.BENCH if kind == "search_bench" else CardPosition.HAND
                )
            )
            pool = p.discard if recovering else p.left
            moved = 0
            for category in r["filters"] if kind == "search_pair" else [r["filter"]]:
                cards = [c for c in pool if matches(c, category) and (not r.get("excludePayment") or c in original_discard)]
                if cards:
                    count = min(
                        r["count"],
                        len(cards),
                        p.benchSize - len(p.bench) if kind == "search_bench" else 60,
                    )
                    chosen = yield from reduce_choose_card_actions(
                        choose_card_actions(
                            p.id,
                            p.id,
                            1 if recovering or category == "any" else 0,
                            count,
                            cards,
                            source=self,
                        ),
                        state,
                    )
                    move_cards(chosen, (p.id, origin), (p.id, destination), state)
                    moved += len(chosen)
                    if destination == CardPosition.HAND and r.get("reveal", True):
                        state.public_reveals.append(
                            {
                                "kind": "search_reveal",
                                "actor": p.id.name,
                                "cards": [c.to_dict() for c in chosen],
                            }
                        )
                    if destination == CardPosition.BENCH:
                        for c in chosen:
                            c.firstTurnPlayed = True
            if kind != "recover_hand":
                shuffle_cards(p.left, state)
            if moved and r.get("thenDraw"):
                move_cards(
                    list(p.left[: r["thenDraw"]]),
                    (p.id, CardPosition.LEFT),
                    (p.id, CardPosition.HAND),
                    state,
                )
        if r.get("endTurn"):
            next_turn(state)


def install(namespace, specs):
    for spec in specs:
        if spec["trainerType"] == "stadium":
            from packages.rules.stadiums import CompiledStadium
            name = "Stadium" + spec["effectKey"].split("-", 1)[1]
            namespace[name] = type(name, (CompiledStadium,), {"spec": spec, "__module__": namespace["__name__"]})
            continue
        cls = {"supporter": SupporterCard, "item": ItemCard, "tool": ToolCard}[
            spec["trainerType"]
        ]
        if spec["mechanic"]["kind"] == "legacy_trainer":
            cls = legacy_handler(spec["mechanic"])
        name = "Trainer" + spec["effectKey"].split("-", 1)[1]
        namespace[name] = type(
            name,
            (CompiledTrainer, cls),
            {"spec": spec, "__module__": namespace["__name__"]},
        )
        if spec["mechanic"]["kind"] in ('setup_doll','fossil'):
            from packages.rules.setup_doll import install as install_doll
            install_doll(namespace[name])


def legacy_handler(rule):
    from packages.rules.effects import Iono, Turo, Ciphermaniac

    return {"Iono": Iono, "Turo": Turo, "Ciphermaniac": Ciphermaniac}[rule["handler"]]

from packages.rules.healing import value as healed

from packages.rules.pokemon_types import has_type

"""Composed trainer effects with explicit costs, owners and hidden choices."""

from types import SimpleNamespace
from ptcg.core.card import EnergyCard, ToolCard
from ptcg.core.enums import CardPosition, Coin, EnergyType, PokemonRule
from ptcg.core.action import choose_card_actions, DiscardStadiumAction
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards, switch_pokemon, flip_coin
from packages.rules.entry_effects import choice, reveal
from packages.rules.trainers import matches
from packages.rules.effects import NumberOption


def playable(r, p, o):
    op = r["op"]
    from packages.rules.expanded_trainers import OPERATIONS as EXPANDED, playable as expanded_playable
    if op in EXPANDED:return expanded_playable(op,p,o)
    from packages.rules.final_trainers import OPERATIONS as FINAL, playable as final_playable
    if op in FINAL:
        return final_playable(op, p, o)
    if op == "kofu":
        return len(p.hand) >= 3
    if op == "pokemon_exchange":
        return any(matches(c, "pokemon") for c in p.hand)
    if op == "bottom_redraw":
        return bool(o.hand)
    if op == "strip_special":
        return any(isinstance(e, EnergyCard) and e.energyType == EnergyType.SPECIAL for c in o.active + o.bench for e in c.attachment)
    if op == "gust_status":
        return any(matches(c, "basic_pokemon") for c in o.bench)
    if op == "look_bench":
        return bool(o.left) and len(o.bench) < o.benchSize
    if op == "hp_guess":
        return any(matches(c, "pokemon") for c in p.hand)
    if op in ("search_discard", "caretaker"):
        return bool(p.left)
    if op == "amarys":
        return bool(p.left)
    if op == "briar":
        return len(o.prize) == 2
    if op == "salvatore":
        return bool(p.left)
    if op == "bottom_draw":
        return len(p.hand) > 1
    if op in ("return_energy", "charisma"):
        return any(isinstance(e, EnergyCard) for c in o.active for e in c.attachment)
    if op in ("invitation", "ortega"):
        return bool(o.hand) and (op != "invitation" or len(o.bench) < o.benchSize)
    if op == "coin_switch":
        return bool(p.bench or o.bench)
    if op == "lucian":
        return len(p.hand) > 1 or bool(o.hand)
    if op == "sandwich":
        from packages.rules.maximum_hp import maximum
        return any(c.hp < maximum(c) for c in p.active)
    if op == "rocket_switch":
        return bool(p.active and p.active[0].name.startswith("Team Rocket's ") and any(c.name.startswith("Team Rocket's ") for c in p.bench))
    if op == "strip":
        return any(removable(e) for c in o.active + o.bench for e in c.attachment) or r.get("all", False)
    if op in ("draw_gust", "draw_recover"):
        return bool(p.left or (o.bench if op == "draw_gust" else any(matches(c,"basic_energy") for c in p.discard)))
    return bool(p.left) if op in {"falkner","top_order","coin_search","student","love_ball","distinct_energy","look_pair","brock","draw_threshold"} else True


def removable(c):
    return isinstance(c, ToolCard) or isinstance(c, EnergyCard) and c.energyType != EnergyType.BASIC


def draw(p, n, s):
    move_cards(list(p.left[:n]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), s)


def select(source, cards, s, actor, minimum=1, maximum=1):
    if not cards:
        return []
    return (yield from reduce_choose_card_actions(choose_card_actions(actor.id, actor.id, min(minimum,len(cards)), min(maximum,len(cards)), cards, indexed=True, source=source),s))


def resolve(source, r, s):
    p, o = current_player(s), opponent_player(s)
    op = r["op"]
    from packages.rules.expanded_trainers import OPERATIONS as EXPANDED, resolve as expanded_resolve
    if op in EXPANDED:
        yield from expanded_resolve(source,r,s)
        return
    from packages.rules.final_trainers import OPERATIONS as FINAL, resolve as final_resolve
    if op in FINAL:
        yield from final_resolve(source, r, s)
    elif op == "search_discard":
        cards = yield from choice(source, list(p.left), s, 0, r["count"])
        move_cards(cards, (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD), s)
        shuffle_cards(p.left, s)
    elif op == "bottom_redraw":
        cards = list(o.hand)
        shuffle_cards(cards, s)
        move_cards(cards, (o.id, CardPosition.HAND), (o.id, CardPosition.LEFT), s)
        draw(o, len(cards), s)
    elif op == "caretaker":
        drew = bool(p.left)
        draw(p, 2, s)
        if drew and any(c.name == "Community Center" for c in s.stadium) and source in p.discard:
            move_cards(source, (p.id, CardPosition.DISCARD), (p.id, CardPosition.LEFT), s)
            shuffle_cards(p.left, s)
    elif op == "pokemon_exchange":
        cards = yield from choice(source, [c for c in p.hand if matches(c, "pokemon")], s, 1, 2)
        reveal(cards, s, p)
        move_cards(cards, (p.id, CardPosition.HAND), (p.id, CardPosition.LEFT), s)
        if cards:
            chosen = yield from choice(source, [c for c in p.left if matches(c, "pokemon")], s, 0, len(cards))
            reveal(chosen, s, p)
            move_cards(chosen, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), s)
        shuffle_cards(p.left, s)
    elif op == "kieran":
        options = [NumberOption(p, 1, "对宝可梦ex、V的招式伤害增加30")]
        if p.bench:
            options.insert(0, NumberOption(p, 0, "交换自己的战斗宝可梦"))
        selected = yield (s.get_obs(p.id), 0, False, {"raw_available_actions": options})
        if selected.value:
            p.timed_modifiers = getattr(p, "timed_modifiers", []) + [{"turn": s.turn_number, "damage": 30, "target": "ex_v"}]
        else:
            cards = yield from choice(source, list(p.bench), s)
            switch_pokemon(p.active[0], cards[0], p)
    elif op == "kofu":
        cards = yield from choice(source, list(p.hand), s, 2, 2)
        if len(cards) == 2:
            first = (yield from choice(source, cards, s))[0]
            move_cards([first] + [c for c in cards if c is not first], (p.id, CardPosition.HAND), (p.id, CardPosition.LEFT), s)
            draw(p, 4, s)
    elif op == "strip_special":
        pool = [e for c in o.active + o.bench for e in c.attachment if isinstance(e, EnergyCard) and e.energyType == EnergyType.SPECIAL]
        cards = yield from choice(source, pool, s)
        from packages.rules.zone_effects import discard_attached
        for target in o.active + o.bench:
            discard_attached(target, [e for e in cards if e in target.attachment], o)
    elif op == "gust_status":
        cards = yield from choice(source, [c for c in o.bench if matches(c, "basic_pokemon")], s)
        if cards:
            switch_pokemon(o.active[0], cards[0], o)
            from packages.rules.status_immunity import apply_status
            from ptcg.core.enums import SpecialCondition
            apply_status(cards[0], SpecialCondition.CONFUSED)
    elif op == "look_bench":
        window = list(o.left[:r["count"]])
        reveal(window, s, o)
        pool = [c for c in window if matches(c, "basic_pokemon")]
        cards = yield from choice(source, pool, s, 0, max(0, o.benchSize-len(o.bench)))
        move_cards(cards, (o.id, CardPosition.LEFT), (o.id, CardPosition.BENCH), s)
        shuffle_cards(o.left, s)
    elif op == "return_pokemon":
        from packages.rules.field_moves import return_stack
        cards = yield from choice(source, p.active + p.bench, s)
        for card in cards:
            return_stack(card, p, "hand")
        from packages.rules.knockouts import resolve_group
        yield from resolve_group(s)
    elif op == "hp_guess":
        from packages.rules.plain import SPECS
        cards = yield from choice(source, [c for c in p.hand if matches(c, "pokemon")], s)
        if cards:
            card = cards[0]
            # Choices disclose only the announced name and public printed HP
            # possibilities, never the selected printing or the rest of hand.
            values = sorted({c["hp"] for c in SPECS if c["name"] == card.name} | set(range(10, 401, 10)))
            options = [NumberOption(o, hp, f"{card.name}：猜测HP为{hp}") for hp in values]
            guess = yield (s.get_obs(o.id), 0, False, {"raw_available_actions": options})
            reveal(cards, s, p)
            draw(o if guess.value == type(card)().hp else p, 4, s)
    elif op == "amarys":
        draw(p, 4, s)
        p.end_turn_effects = getattr(p, "end_turn_effects", []) + [{"turn": s.turn_number, "name": source.name, "discardHandAtLeast": 5}]
    elif op == "briar":
        p.tera_bonus_prize_turn = s.turn_number
    elif op == "salvatore":
        from ptcg.core.card import PokemonCard
        from ptcg.core.action import EvolvePokemonAction
        from ptcg.core.reducer import reduce_evolve_pokemon_action
        targets = yield from choice(source,p.active+p.bench,s)
        if targets:
            target = targets[0]
            cards = yield from choice(source,[c for c in p.left if isinstance(c,PokemonCard) and not getattr(c,"ability",[]) and getattr(c,"evolveFrom",[])[:1] == [target.name]],s,0,1)
            if cards:
                move_cards(cards,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),s)
                reduce_evolve_pokemon_action(EvolvePokemonAction(p.id,cards[0],target),s)
        shuffle_cards(p.left,s)
    elif op == "bottom_draw":
        cards = yield from choice(source,list(p.hand),s)
        move_cards(cards,(p.id,CardPosition.HAND),(p.id,CardPosition.LEFT),s)
        draw(p,max(0,5-len(p.hand)),s)
    elif op in ("return_energy", "charisma"):
        from packages.rules.energy_selection import choose_units
        from packages.rules.core_fixes import refresh_energy
        target = o.active[0]
        from packages.rules.zone_guards import trainer_immune, hand_return_forbidden
        if trainer_immune(target, s) or op == "charisma" and hand_return_forbidden(o, s):
            return
        cards = yield from choose_units(target,1,SimpleNamespace(source=source),s)
        for c in cards:
            target.attachment.remove(c)
            fresh = type(c)()
            vars(c).clear()
            vars(c).update(vars(fresh))
            destination = o.hand if op == "charisma" else o.left
            destination.insert(len(destination) if op == "charisma" else 0,c)
            for i, e in enumerate(destination):
                e.index, e.cardPosition = i+1, CardPosition.HAND if op == "charisma" else CardPosition.LEFT
        for i,e in enumerate(target.attachment):
            e.index = i+1
        target.dynamic_energy = True
        refresh_energy(target)
        if op == "charisma" and cards:
            energy = yield from choice(source,[e for e in p.hand if isinstance(e,EnergyCard)],s)
            if energy:
                move_cards(energy,(p.id,CardPosition.HAND),(p.id,CardPosition.ACTIVE_ATTACHMENT,1),s)
                p.active[0].dynamic_energy = True
                refresh_energy(p.active[0])
    elif op == "invitation":
        reveal(list(o.hand),s,o)
        cards = yield from choice(source,[c for c in o.hand if matches(c,"basic_pokemon")],s)
        if cards and len(o.bench) < o.benchSize:
            move_cards(cards,(o.id,CardPosition.HAND),(o.id,CardPosition.BENCH),s)
            cards[0].hasBeenOnField = False
            switch_pokemon(o.active[0], cards[0], o)
    elif op == "ortega":
        reveal(list(o.hand),s,o)
        cards = yield from choice(source,list(o.hand),s)
        move_cards(cards,(o.id,CardPosition.HAND),(o.id,CardPosition.LEFT),s)
        if cards and o.left:
            decision = yield (s.get_obs(o.id),0,False,{"raw_available_actions":[NumberOption(o,0,"不抽牌"),NumberOption(o,1,"抽取1张牌")]})
            if decision.value:
                draw(o,1,s)
    elif op in ("falkner", "draw_threshold", "draw_gust", "draw_recover"):
        draw(p,3 if op == "draw_gust" else 2,s)
        if op == "falkner" and any(c.playedFrom == p.id for c in s.stadium) or op == "draw_threshold" and len(p.hand) >= 10:
            draw(p,2,s)
        if op == "draw_gust":
            cards = yield from select(source,list(o.bench),s,o)
            if cards:
                switch_pokemon(o.active[0],cards[0],o)
        if op == "draw_recover":
            cards = yield from choice(source,[c for c in p.discard if matches(c,"basic_energy")],s)
            reveal(cards,s,p)
            move_cards(cards,(p.id,CardPosition.DISCARD),(p.id,CardPosition.HAND),s)
    elif op == "top_order":
        window = list(p.left[:5])
        cards = yield from choice(source,window,s,0,len(window))
        move_cards(cards,(p.id,CardPosition.LEFT),(p.id,CardPosition.DISCARD),s)
        rest = [c for c in window if c not in cards]
        ordered = []
        while rest:
            c = (yield from choice(source,rest,s))[0]
            rest.remove(c)
            ordered.append(c)
        p.left[:] = ordered + [c for c in p.left if c not in ordered]
        for i,c in enumerate(p.left):
            c.index = i+1
    elif op in ("coin_search", "student", "love_ball", "brock"):
        count, category = 1,"pokemon"
        if op == "coin_search":
            heads = flip_coin(s) == Coin.HEAD
            category = "evolution" if r.get("rocket") and heads else "pokemon" if heads else "basic_pokemon"
            count = 1 if r.get("rocket") or not heads else 2
        elif op == "student":
            category = "no_rule"
            count += sum(c is not source and c.name == source.name for c in p.discard)
        elif op == "brock":
            decision = yield (s.get_obs(p.id),0,False,{"raw_available_actions":[NumberOption(p,0,"检索最多2张基础宝可梦"),NumberOption(p,1,"检索1张进化宝可梦")]})
            category, count = ("basic_pokemon",2) if not decision.value else ("evolution",1)
        pool = [c for c in p.left if matches(c,category) and (not r.get("rocket") or c.name.startswith("Team Rocket's ")) and (op != "love_ball" or c.name in {x.name for x in o.active+o.bench})]
        cards = yield from choice(source,pool,s,0,count)
        reveal(cards,s,p)
        move_cards(cards,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),s)
        shuffle_cards(p.left,s)
    elif op == "coin_switch":
        player = o if flip_coin(s) == Coin.HEAD else p
        cards = yield from choice(source,list(player.bench),s)
        if cards:
            switch_pokemon(player.active[0],cards[0],player)
    elif op == "lucian":
        any_cards = bool(p.hand or o.hand)
        for player in (p,o):
            cards = list(player.hand)
            shuffle_cards(cards,s)
            move_cards(cards,(player.id,CardPosition.HAND),(player.id,CardPosition.LEFT),s)
        if any_cards:
            for player in (p,o):
                draw(player,6 if flip_coin(s) == Coin.HEAD else 3,s)
    elif op == "distinct_energy":
        cards, types = [],set()
        while len(cards) < r.get("count",8):
            pool = [c for c in p.left if matches(c,"basic_energy") and c.cardType not in types]
            selected = yield from choice(source,pool,s,0,1)
            if not selected:
                break
            cards += selected
            types.add(selected[0].cardType)
        reveal(cards,s,p)
        if cards and r.get("attachOther"):
            hand = yield from choice(source,cards,s)
            move_cards(hand,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),s)
            for c in [x for x in cards if x not in hand]:
                target = (yield from choice(source,p.active+p.bench,s))[0]
                move_cards(c,(p.id,CardPosition.LEFT),(p.id,CardPosition.ACTIVE_ATTACHMENT if target in p.active else CardPosition.BENCH_ATTACHMENT,target.index),s)
                from packages.rules.core_fixes import refresh_energy
                target.dynamic_energy = True
                refresh_energy(target)
        else:
            move_cards(cards,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),s)
        shuffle_cards(p.left,s)
    elif op == "look_pair":
        window = list(p.left[:7])
        for category in ("pokemon","trainer"):
            cards = yield from choice(source,[c for c in window if matches(c,category)],s,0,1)
            reveal(cards,s,p)
            move_cards(cards,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),s)
        shuffle_cards(p.left,s)
    elif op == "strip":
        targets = [c for c in o.active+o.bench if any(removable(e) for e in c.attachment)]
        if not r.get("all"):
            targets = yield from choice(source,targets,s)
        from packages.rules.zone_effects import discard_attached
        for target in targets:
            cards = [e for e in target.attachment if removable(e)]
            if not r.get("all"):
                selected = []
                for cls in (ToolCard,EnergyCard):
                    selected += yield from choice(source,[c for c in cards if isinstance(c,cls)],s)
                cards = selected
            discard_attached(target,cards,o)
        if r.get("all"):
            for c in list(s.stadium):
                yield from c.reduce_action(DiscardStadiumAction(c.playedFrom,c),s)
    elif op == "armor":
        p.timed_modifiers = [x for x in getattr(p,"timed_modifiers",[]) if x.get("origin") != source.name]
        p.timed_modifiers.append({"turn":s.turn_number+1,"armor":30,"origin":source.name})
    elif op == "sandwich":
        from packages.rules.healing import value
        c = p.active[0]
        c.hp = value(c,100 if c.name.startswith("Arven's ") else 30,s,record=True)
    elif op == "bomb":
        cards = (yield from choice(source,o.active+o.bench,s)) if flip_coin(s) == Coin.HEAD else p.active
        for c in cards:
            c.hp -= 20
    elif op == "rocket_switch":
        cards = yield from choice(source,[c for c in p.bench if c.name.startswith("Team Rocket's ")],s)
        if cards:
            switch_pokemon(p.active[0],cards[0],p)
            cards = yield from choice(source,list(o.bench),s)
            if cards:
                switch_pokemon(o.active[0],cards[0],o)
    from packages.rules.maximum_hp import settle
    yield from settle(s)

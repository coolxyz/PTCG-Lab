"""Attack costs and optional bonuses keep pre-damage and post-damage timing."""

from ptcg.core.enums import CardPosition, CardType
from ptcg.core.card import EnergyCard, ToolCard, PokemonCard
from ptcg.core.reducer import reduce_attack_damage
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards, next_turn
from packages.rules.entry_effects import choice
from packages.rules.zone_effects import matches, discard_attached


def pay(r, action, state):
    p = current_player(state)
    source = action.source
    cost = r.get("cost", {})
    kind = cost.get("kind", "none")
    if kind == "face_up_prize":
        from packages.rules.prizes import choose
        selected = yield from choose(p, state, 1, source, [c for c in p.prize if not getattr(c, "prize_face_up", False)], optional=True)
        if selected:
            selected[0].prize_face_up = True
        return bool(selected)
    if kind == "hand":
        cards = [c for c in p.hand if matches(c,cost.get("filter","any"))]
        if len(cards) < cost.get("count", 1):
            return False
    elif kind == "all_hand":
        cards = list(p.hand)
    elif kind == "stadium":
        cards = list(state.stadium)
    elif kind == "tools":
        cards = [c for c in source.attachment if isinstance(c,ToolCard) and (not cost.get("name") or c.name==cost["name"])]
    elif kind == "bench_return":
        cards = [c for c in p.bench if c.name==cost["name"]]
    elif kind == "energy":
        from packages.rules.core_fixes import refresh_energy
        refresh_energy(source)
        cards = [c for c in source.attachment if isinstance(c,EnergyCard) and (not cost.get("name") or c.name == cost["name"]) and (not cost.get("type") or any(energy_matches(e, cost["type"]) for e in c.provides))]
        units = sum(sum(energy_matches(e, cost["type"]) for e in c.provides) if cost.get("type") else len(c.provides) for c in cards)
        if units < (1 if cost.get("partial") else cost.get("count",1)): return False
    elif kind == "all_energy_hand":
        from packages.rules.zone_guards import hand_return_forbidden
        if hand_return_forbidden(p, state):
            return False
        cards = [c for c in source.attachment if isinstance(c, EnergyCard)]
    elif kind == "none":
        cards = [source]
    else:
        raise ValueError("Unknown attack cost: "+kind)
    if not cards:
        return False
    if r.get("optional") and not (yield from choice(source,[source],state,0,1)):
        return False
    if kind == "hand":
        cards = yield from choice(source,cards,state,cost.get("count",1),cost.get("count",1))
        move_cards(cards,(p.id,CardPosition.HAND),(p.id,CardPosition.DISCARD),state)
    elif kind == "all_hand":
        move_cards(cards,(p.id,CardPosition.HAND),(p.id,CardPosition.DISCARD),state)
    elif kind == "tools":
        discard_attached(source,cards,p)
    elif kind == "all_energy_hand":
        from packages.rules.core_fixes import refresh_energy
        for card in cards:
            source.attachment.remove(card)
            vars(card).clear()
            vars(card).update(vars(type(card)()))
            p.hand.append(card)
            card.cardPosition, card.index = CardPosition.HAND, len(p.hand)
        source.dynamic_energy = True
        refresh_energy(source)
    elif kind == "stadium":
        from packages.rules.core_fixes import discard_card
        for c in cards:
            owner=state.player1 if c.playedFrom==state.player1.id else state.player2
            state.stadium.remove(c)
            discard_card(owner,c)
    elif kind == "bench_return":
        from packages.rules.field_moves import return_stack
        selected=(yield from choice(source,cards,state))[0]
        return_stack(selected,p,"left")
        shuffle_cards(p.left,state)
    elif kind == "energy":
        from packages.rules.energy_selection import choose_units
        selected = (yield from choice(source, cards, state, 1, 1)) if cost.get("name") else (yield from choose_units(source,cost.get("count",1),action,state,cost.get("type")))
        if cost.get("destination") == "left":
            for c in selected:
                source.attachment.remove(c)
                fresh=type(c)();vars(c).clear();vars(c).update(vars(fresh))
                c.cardPosition = CardPosition.LEFT
                p.left.append(c)
            shuffle_cards(p.left,state)
            from packages.rules.core_fixes import refresh_energy
            source.dynamic_energy=True;refresh_energy(source)
            for i,c in enumerate(source.attachment):c.index=i+1
        else:
            discard_attached(source,selected,p)
    return True


def resolve(r, action, state):
    from packages.rules.plain import PlainPokemon
    for child in r.get("beforeEffects", []):
        yield from PlainPokemon.resolve_mechanic(action.source, child, action, state)
    condition = True
    if r.get("condition"):
        from packages.rules.attack_math import damage
        condition = bool(damage({**r["condition"], "mode": "multiply", "factor": 1}, action, state))
    if r.get("requiresCondition") and not condition:
        next_turn(state)
        return
    if r.get("damageExpression"):
        from packages.rules.attack_math import damage
        action.attack.damage = damage(r["damageExpression"], action, state)
    paid = False
    if r.get("selfCounters"):
        from packages.rules.effects import NumberOption
        p=current_player(state)
        selected = yield (state.get_obs(p.id),0,False,{"raw_available_actions":[NumberOption(p,n,f"放置{n}个伤害指示物（{n*10}点）") for n in range(r["selfCounters"]+1)]})
        action.source.hp -= selected.value*10
        action.attack.damage = selected.value*r["factor"]
    elif r.get("top"):
        p,o=current_player(state),opponent_player(state)
        discarded=[]
        for owner in (p,o) if r.get("both") else (p,):
            cards=list(owner.left[:r["top"]])
            discarded.extend(cards)
            move_cards(cards,(owner.id,CardPosition.LEFT),(owner.id,CardPosition.DISCARD),state)
        def eligible(c):
            if r.get("filter") == "energy":return isinstance(c,EnergyCard)
            return isinstance(c,PokemonCard) and (not r.get("prefix") or c.name.startswith(r["prefix"])) and ("retreat" not in r or len(type(c)().retreat)==r["retreat"])
        action.attack.damage=(action.attack.damage if r["mode"]=="add" else 0)+sum(eligible(c) for c in discarded)*r["factor"]
    elif not r.get("after") and condition:
        paid=yield from pay(r,action,state)
        if r.get("required") and not paid:
            next_turn(state)
            return
        if paid:action.attack.damage+=r.get("bonus",0)

    def after_damage(action,state):
        did_pay=(yield from pay(r,action,state)) if r.get("after") and condition else paid
        if did_pay:
            for child in r.get("effects",[]):
                yield from PlainPokemon.resolve_mechanic(action.source,child,action,state)
    yield from reduce_attack_damage(action,state,after_damage=after_damage)
    next_turn(state)

from packages.rules.energy_units import matches as energy_matches

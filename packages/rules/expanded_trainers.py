"""Closed trainer compositions for the remaining supported format cards."""
from ptcg.core.card import EnergyCard
from ptcg.core.enums import CardPosition, Coin, Stage, SpecialCondition
from ptcg.core.action import EvolvePokemonAction
from ptcg.core.reducer import reduce_evolve_pokemon_action
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards, flip_coin, switch_pokemon
from packages.rules.entry_effects import choice, reveal, transfer
from packages.rules.trainers import matches

OPERATIONS={'double_coin_search','draw_inspect_prizes','parasol_redraw','ex_team_bonus','supporter_bell','energy_hand','order_or_bottom','switch_transfer','discard_search_three','heal_cure'}


def playable(op,p,o):
    if op in ('double_coin_search','supporter_bell','order_or_bottom'):
        return bool(p.left)
    if op=='draw_inspect_prizes':
        return bool(p.left or any(not getattr(c,'prize_face_up',False) for c in p.prize))
    if op=='parasol_redraw':return bool(p.left or len(p.hand)>1)
    if op=='switch_transfer':return bool(p.active and p.bench)
    if op=='energy_hand':return any(isinstance(e,EnergyCard) for c in o.active+o.bench for e in c.attachment)
    if op=='heal_cure':
        from packages.rules.maximum_hp import maximum
        return any(c.hp<maximum(c) or getattr(c,'poisoned',False) or getattr(c,'burned',False) or getattr(c,'special_condition',SpecialCondition.NONE)!=SpecialCondition.NONE for c in p.active+p.bench)
    return True


def resolve(source,r,state):
    p,o=current_player(state),opponent_player(state);op=r['op']
    from packages.rules.trainer_operations import draw
    if op=='double_coin_search':
        heads=sum(flip_coin(state)==Coin.HEAD for _ in range(2))
        if heads==2:
            picked=yield from choice(source,list(p.left),state,0,1)
            move_cards(picked,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),state)
            shuffle_cards(p.left,state)
    elif op=='draw_inspect_prizes':
        draw(p,2,state)
        from packages.rules.expanded_entry import inspect
        yield from inspect(source,[c for c in p.prize if not getattr(c,'prize_face_up',False)],state,p)
    elif op=='parasol_redraw':
        count=8 if p.firstTurn and p.id!=state.starting_player else 4
        move_cards(list(p.hand),(p.id,CardPosition.HAND),(p.id,CardPosition.LEFT),state)
        shuffle_cards(p.left,state);draw(p,count,state)
    elif op=='ex_team_bonus':
        p.timed_modifiers=getattr(p,'timed_modifiers',[])+[{'turn':state.turn_number,'damage':40,'target':'ex'}]
    elif op=='supporter_bell':
        picked=yield from choice(source,[c for c in p.left if matches(c,'supporter')],state,0,1)
        reveal(picked,state,p)
        move_cards(picked,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),state)
        shuffle_cards(p.left,state)
    elif op=='energy_hand':
        cards=[c for c in o.active+o.bench if any(isinstance(e,EnergyCard) for e in c.attachment)]
        picked=yield from choice(source,cards,state)
        if picked:
            target=picked[0]
            selected=yield from choice(source,[e for e in target.attachment if isinstance(e,EnergyCard)],state)
            zone=CardPosition.ACTIVE_ATTACHMENT if target in o.active else CardPosition.BENCH_ATTACHMENT
            move_cards(selected,(o.id,zone,target.index),(o.id,CardPosition.HAND),state)
            from packages.rules.core_fixes import refresh_energy
            refresh_energy(target)
    elif op=='order_or_bottom':
        from packages.rules.expanded_entry import inspect
        from packages.rules.effects import NumberOption
        window=list(p.left[:3])
        yield from inspect(source,window,state,p)
        answer=yield(state.get_obs(p.id),0,False,{'raw_available_actions':[NumberOption(p,0,'放回牌库底'),NumberOption(p,1,'排列牌库顶')]})
        if answer.value:
            ordered=[];rest=list(window)
            while rest:
                chosen=(yield from choice(source,rest,state))[0];rest.remove(chosen);ordered.append(chosen)
            p.left[:len(window)]=ordered
        else:
            shuffle_cards(window,state)
            move_cards(window,(p.id,CardPosition.LEFT),(p.id,CardPosition.LEFT),state)
        for i,c in enumerate(p.left):c.index=i+1
    elif op=='switch_transfer':
        if p.active and p.bench:
            old=p.active[0];picked=yield from choice(source,list(p.bench),state)
            if picked:
                new=picked[0];switch_pokemon(old,new,p)
                cards=[e for e in old.attachment if isinstance(e,EnergyCard)]
                selected=yield from choice(source,cards,state,0,len(cards))
                transfer(old,new,selected)
    elif op=='discard_search_three':
        move_cards(list(p.hand),(p.id,CardPosition.HAND),(p.id,CardPosition.DISCARD),state)
        for category in ('pokemon','supporter','basic_energy'):
            picked=yield from choice(source,[c for c in p.left if matches(c,category)],state,0,1)
            reveal(picked,state,p)
            move_cards(picked,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),state)
        shuffle_cards(p.left,state)
    elif op=='heal_cure':
        from packages.rules.healing import value
        from packages.rules.maximum_hp import maximum
        cards=[c for c in p.active+p.bench if c.hp<maximum(c) or getattr(c,'poisoned',False) or getattr(c,'burned',False) or getattr(c,'special_condition',SpecialCondition.NONE)!=SpecialCondition.NONE]
        picked=yield from choice(source,cards,state)
        for c in picked:
            c.hp=value(c,60,state,record=True)
            for attr in ('special_condition','poisoned','burned','poison_damage','sleep_coins','confusion_damage'):
                if hasattr(c,attr):delattr(c,attr)


def great_tree(source,state):
    p=current_player(state)
    cards=[c for c in p.active+p.bench if c.stage==Stage.BASIC and not c.firstTurnPlayed and getattr(c,'evolution_blocked_turn',None)!=state.turn_number]
    if getattr(p,'evolution_blocked_turn',None)==state.turn_number:return
    if p.firstTurn or not cards:return
    chosen=yield from choice(source,cards,state)
    if not chosen:return
    target=chosen[0]
    from packages.rules.pokemon_replacement import permitted
    for stage in (Stage.STAGE_1,Stage.STAGE_2):
        pool=[c for c in p.left if getattr(c,'stage',None)==stage and getattr(c,'evolveFrom',[])[:1]==[target.name] and permitted(c)]
        picked=yield from choice(source,pool,state,0,1)
        if not picked:break
        evolution=picked[0]
        move_cards(evolution,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),state)
        reduce_evolve_pokemon_action(EvolvePokemonAction(p.id,evolution,target),state)
        target=evolution
    shuffle_cards(p.left,state)

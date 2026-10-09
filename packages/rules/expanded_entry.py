"""Reviewed entry/activated compositions for the final GHIJ source batch."""
from ptcg.core.card import PokemonCard, ToolCard
from ptcg.core.enums import CardPosition, PokemonPosition, Stage, Coin, SpecialCondition
from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards, flip_coin, can_attach_tool
from packages.rules.entry_effects import choice


def inspect(source, cards, state, owner):
    p = current_player(state)
    if cards:
        yield from reduce_choose_card_actions(choose_card_actions(p.id,owner.id,0,0,list(cards),source=source),state)


def cure_one(source, target, state):
    from packages.rules.effects import NumberOption
    attrs = [a for a in ('poisoned','burned') if getattr(target,a,False)]
    condition = getattr(target,'special_condition',SpecialCondition.NONE)
    if condition != SpecialCondition.NONE:
        attrs.append('special_condition')
    if not attrs:
        return
    labels = {'poisoned':'中毒','burned':'灼伤','special_condition':{'ASLEEP':'睡眠','PARALYZED':'麻痹','CONFUSED':'混乱'}.get(condition.name,'特殊状态')}
    p=current_player(state)
    answer = yield (state.get_obs(p.id),0,False,{'raw_available_actions':[NumberOption(p,i,'解除'+labels[a]) for i,a in enumerate(attrs)]})
    attr=attrs[answer.value]
    delattr(target,attr)
    for extra in {'poisoned':['poison_damage'],'burned':[],'special_condition':['sleep_coins','confusion_damage']}[attr]:
        if hasattr(target,extra):delattr(target,extra)


def resolve(source,r,state):
    p,o=current_player(state),opponent_player(state)
    op=r['op']
    if op=='mill_self':
        move_cards(list(p.left[:r['count']]),(p.id,CardPosition.LEFT),(p.id,CardPosition.DISCARD),state)
    elif op=='heal_cure_one':
        if p.active:
            from packages.rules.healing import value
            target=p.active[0]
            target.hp=value(target,r['amount'],state,record=True)
            yield from cure_one(source,target,state)
    elif op=='deploy_opponent':
        yield from inspect(source,o.hand,state,o)
        cards=[c for c in o.hand if isinstance(c,PokemonCard) and c.stage==Stage.BASIC]
        selected=yield from choice(source,cards,state,0,max(0,o.benchSize-len(o.bench)))
        move_cards(selected,(o.id,CardPosition.HAND),(o.id,CardPosition.BENCH),state)
        for c in selected:
            c.firstTurnPlayed=True
            c.position=PokemonPosition.BENCH
    elif op=='attach_tool':
        if can_attach_tool(source):
            cards=[c for c in p.left if isinstance(c,ToolCard)]
            picked=yield from choice(source,cards,state,0,1)
            for c in picked:
                move_cards(c,(p.id,CardPosition.LEFT),(p.id,CardPosition.BENCH_ATTACHMENT,source.index),state)
                c.hasAttached=True;c.attachedTo=[source]
        shuffle_cards(p.left,state)
    elif op=='coin_shuffle_hand':
        count=min(sum(flip_coin(state)==Coin.HEAD for _ in range(r['flips'])),len(o.hand))
        if count:
            selected=yield from reduce_choose_card_actions(choose_card_actions(p.id,o.id,count,count,list(o.hand),indexed=True,hidden=True,source=source),state)
            yield from inspect(source,selected,state,o)
            move_cards(selected,(o.id,CardPosition.HAND),(o.id,CardPosition.LEFT),state)
            shuffle_cards(o.left,state)
    elif op=='transform_start':
        cards=[c for c in p.left if isinstance(c,PokemonCard) and c.stage==Stage.BASIC and c.name!=source.name]
        selected=yield from choice(source,cards,state,0,1)
        if selected:
            from packages.rules.core_fixes import discard_pokemon
            discard_pokemon(p,source)
            move_cards(selected,(p.id,CardPosition.LEFT),(p.id,CardPosition.ACTIVE),state)
            selected[0].firstTurnPlayed=True
            selected[0].position=PokemonPosition.ACTIVE
        shuffle_cards(p.left,state)
    else:
        raise ValueError('Unknown reviewed entry operation: '+op)

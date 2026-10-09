"""Remaining source-reviewed attack operations with explicit physical targets."""
from ptcg.core.card import PokemonCard, EnergyCard
from ptcg.core.enums import CardPosition, CardType, Stage, SpecialCondition
from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards
from packages.rules.entry_effects import choice, reveal
from packages.rules.protection import blocked

OPERATIONS = {'shuffle_pair', 'distinct_types_search', 'choose_weakness', 'choose_status',
              'damaged_spread', 'stage_energy_return', 'bottom_draw', 'match_hand_draw',
              'opponent_shuffle_hand', 'distinct_counters', 'bench_attack', 'supporter_bottom'}


def resolve(r, action, state):
    p, o = current_player(state), opponent_player(state)
    source, target = action.source, action.target
    op = r['operation']
    if op == 'bench_attack':
        return
    if op == 'supporter_bottom':
        from packages.rules.expanded_entry import inspect
        from packages.rules.trainers import matches
        yield from inspect(source,list(o.hand),state,o)
        selected=yield from choice(source,[c for c in o.hand if matches(c,'supporter')],state)
        move_cards(selected,(o.id,CardPosition.HAND),(o.id,CardPosition.LEFT),state)
    elif op == 'shuffle_pair':
        if not o.bench:
            return
        selected = yield from choice(source, list(o.bench), state)
        from packages.rules.field_moves import return_stack
        if selected and not blocked(selected[0], state, 'effects', source):
            return_stack(selected[0], o, 'left')
            shuffle_cards(o.left, state)
        return_stack(source, p, 'left')
        shuffle_cards(p.left, state)
        action.group_damage_targets = []
    elif op == 'distinct_types_search':
        from packages.rules.pokemon_types import types
        selected, used = [], set()
        for _ in range(r['count']):
            pool = [c for c in p.left if isinstance(c, PokemonCard) and not types(c) & used]
            picked = yield from choice(source, pool, state, 0, 1)
            if not picked:
                break
            selected += picked
            used.update(types(picked[0]))
        reveal(selected, state, p)
        move_cards(selected, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
        shuffle_cards(p.left, state)
    elif op == 'choose_weakness':
        from packages.rules.effects import NumberOption
        values = ['GRASS', 'FIRE', 'WATER', 'LIGHTNING', 'PSYCHIC', 'FIGHTING', 'DARK', 'METAL', 'DRAGON']
        labels = ['草', '火', '水', '雷', '超', '斗', '恶', '钢', '龙']
        answer = yield (state.get_obs(p.id), 0, False, {'raw_available_actions': [NumberOption(p, i, '弱点：'+label) for i, label in enumerate(labels)]})
        if not blocked(target, state, 'effects', source):
            target.weakness_override = {'type':values[answer.value], 'active':True}
    elif op == 'choose_status':
        from packages.rules.effects import NumberOption
        values = ['POISONED', 'BURNED', 'ASLEEP', 'PARALYZED', 'CONFUSED']
        labels = ['中毒', '灼伤', '睡眠', '麻痹', '混乱']
        answer = yield (state.get_obs(p.id), 0, False, {'raw_available_actions': [NumberOption(p, i, label) for i, label in enumerate(labels)]})
        from packages.rules.plain import PlainPokemon
        yield from PlainPokemon.resolve_mechanic(source, {'kind':'special_status','coin':False,'target':'opponent','status':values[answer.value]}, action, state)
    elif op == 'damaged_spread':
        from packages.rules.maximum_hp import maximum
        from ptcg.core.reducer import _calculate_damage
        from packages.rules.core_fixes import shield_damage
        from packages.rules.damage_events import deal
        targets = [c for owner in (p, o) for c in (owner.bench if r.get('benchOnly') else owner.active + owner.bench) if c is not source and c.hp < maximum(c)]
        for c in targets:
            amount = shield_damage(c, r['amount'], state, source) if c in p.bench + o.bench else _calculate_damage(source, c, r['amount'], state)
            deal(source, c, amount, state)
        action.group_damage_targets = list(set(getattr(action, 'group_damage_targets', []) + targets))
    elif op == 'stage_energy_return':
        if target.stage != Stage.STAGE_2 or blocked(target, state, 'effects', source):
            return
        cards = [c for c in target.attachment if isinstance(c, EnergyCard)]
        # Optional effect, but choosing to use it returns two when available.
        from packages.rules.effects import NumberOption
        if not cards:
            return
        answer = yield (state.get_obs(p.id), 0, False, {'raw_available_actions':[NumberOption(p,0,'不返回能量'),NumberOption(p,1,'返回能量')]})
        if answer.value:
            from packages.rules.energy_selection import choose_units
            picked = yield from choose_units(target, 2, action, state)
            move_cards(picked, (o.id, CardPosition.ACTIVE_ATTACHMENT, target.index), (o.id, CardPosition.HAND), state)
            from packages.rules.core_fixes import refresh_energy
            refresh_energy(target)
    elif op == 'bottom_draw':
        picked = list(reversed(p.left[-r['count']:]))
        move_cards(picked, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
    elif op == 'match_hand_draw':
        count = len(o.hand)
        move_cards(list(p.hand), (p.id, CardPosition.HAND), (p.id, CardPosition.LEFT), state)
        shuffle_cards(p.left, state)
        move_cards(list(p.left[:count]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
    elif op == 'opponent_shuffle_hand':
        count = min(r['count'], len(o.hand))
        if count:
            picked = yield from reduce_choose_card_actions(choose_card_actions(o.id,o.id,count,count,list(o.hand),source=source),state)
            move_cards(picked,(o.id,CardPosition.HAND),(o.id,CardPosition.LEFT),state)
            shuffle_cards(o.left,state)
    elif op == 'distinct_counters':
        picked = yield from choice(source, list(o.active + o.bench), state, r['count'], r['count'])
        for c in picked:
            if not blocked(c, state, 'counters', source):
                c.hp -= r['amount']
        action.group_damage_targets = []

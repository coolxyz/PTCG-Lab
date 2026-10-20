"""Frozen nonterminal Iono rule."""
from apps.api.expert_policies import grimmsnarl_legacy_v1 as legacy
from packages.battle.h2_v4.tempo import estimate

def iono_before_ko(dto, chosen):
    if chosen.get('actionType') != 'AttackAction': return None
    own, other = dto['observation']['self'], dto['observation']['opponent']
    active, enemy = own['active'][0], other['active'][0]
    amount = estimate(chosen.get('attack', {}), active, own, other)
    if amount < enemy.get('hp', 9999) or amount <= 0: return None
    if enemy.get('prize',1) >= own['prize_count'] or not other['bench']: return None
    flags = own.get('turnFlags', {})
    if flags.get('supporterPlayed') or own['prize_count'] - len(own['hand']) < 2:
        return None
    if other.get('hand_count',0) <= other['prize_count']: return None
    options = dto['decision']['options']
    if any((o['actionType']=='EvolvePokemonAction' and o.get('source') in (legacy.MAIN, legacy.MID))
           or (o['actionType']=='UseItemAction' and o.get('source')=='Rare Candy') for o in options):
        return None
    return next((o for o in options if o['actionType']=='UseSupporterAction' and o.get('source')=='Iono'), None)

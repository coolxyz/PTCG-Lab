"""Current-turn prize combinations and Candy dependencies, from product v1."""
import time
from apps.api.expert_policies import grimmsnarl_legacy_v1 as previous
from packages.battle.h2_v4.policy import deficit
from packages.battle.h2_v4.visible_math import SPECS, counters, damage
from apps.api.expert_policies.grimmsnarl_iono import iono_before_ko
from packages.simulation.a2 import legal

PROFILE=previous.PROFILE
VERSION='h2-grimmsnarl-prize-chain-v5'
MAIN,MID,BASIC=previous.MAIN,previous.MID,previous.BASIC
GUSTS={"Boss's Orders",'Counter Catcher'}

def rules(c):return SPECS.get(c.get('id'),{}).get('abilities',[])
def candidate_card(c):return {**c['card'],'ref':c.get('boardRef',c['ref'])}
def pokemon_rule(c):return c.get('pokemonRule') or SPECS.get(c.get('id'),{}).get('pokemonRule') or {'TWM-200':'TERA'}.get(c.get('id'))

def rule_matches(rule, target, attacker, bench):
    if rule.get('zone')=='bench' and not bench:return False
    if rule.get('noRuleBox') and (target.get('pokemonType','NORMAL')!='NORMAL' or pokemon_rule(target) in ('TERA','RADIANT')):return False
    if rule.get('targetStage') and target.get('stage')!=rule['targetStage']:return False
    if rule.get('targetPrefix') and not target.get('name','').startswith(rule['targetPrefix']):return False
    if rule.get('hasEnergy') and not target.get('energy'):return False
    source=rule.get('source')
    if source in ('ex','ex_v') and attacker.get('pokemonType') not in (('EX',) if source=='ex' else ('EX','V','VSTAR')):return False
    if source=='basic' and attacker.get('stage')!='BASIC':return False
    if source=='basic_ex' and (attacker.get('stage')!='BASIC' or attacker.get('pokemonType')!='EX'):return False
    if source=='ability' and not attacker.get('abilities'):return False
    if rule.get('exceptSourceName')==attacker.get('name'):return False
    if rule.get('exceptType') in attacker.get('effectiveTypes',[attacker.get('cardType')]):return False
    if rule.get('sourceType') and rule['sourceType'] not in attacker.get('effectiveTypes',[attacker.get('cardType')]):return False
    return True

def protected(target, team, attacker, bench):
    if target.get('attackProtection',{}).get('damage'):return True
    if bench and pokemon_rule(target)=='TERA':return True
    for holder in team['active']+team['bench']:
        for r in rules(holder):
            if r.get('kind')=='protection' and r.get('damage') and (holder.get('ref')==target.get('ref') or r.get('scope')=='team'):
                # Conservatively retain public protection when suppression is
                # not represented; never invent a guaranteed extra prize.
                if r.get('holderActive') and holder not in team['active']:continue
                if rule_matches(r,target,attacker,bench):return True
    return False

def shadow(own):
    active=(own['active'] or [{}])[0]
    flags=own.get('turnFlags',{})
    if active.get('name')!=MAIN or active.get('attackBlocked') or flags.get('attacksBlocked'):return None
    return next((a for a in active.get('attacks',[]) if a.get('name')=='Shadow Bullet' and deficit(a.get('cost',[]),active.get('energy',[]))==0 and a['name'] not in active.get('limitedAttacks',[])),None)

def bench_hit(target, own, other):
    active=(own['active'] or [{}])[0]
    if protected(target,other,active,True):return 0
    # Bench hits ignore weakness/resistance; shield/temporary flags still apply.
    amount=max(0,30-target.get('attackDamageReduction',0))
    return 0 if amount<=target.get('preventAttackDamageAtMost',-1) else amount

def exchange(own, other, front):
    a=shadow(own)
    if not a:return 0
    active=own['active'][0]
    remaining=[c for c in other['active']+other['bench'] if c['ref']!=front['ref']]
    projected={**other,'active':[front],'bench':remaining}
    front_damage=0 if protected(front,projected,active,False) else damage(a,active,front,own,projected)
    prizes=front.get('prize',1) if 0<front.get('hp',9999)<=front_damage else 0
    return prizes+max((c.get('prize',1) for c in remaining if 0<c.get('hp',9999)<=bench_hit(c,own,projected)),default=0)

def gust_reachable(card, other):
    # A legal target may still ignore a Trainer's effect on resolution.
    return not any(r.get('kind')=='trainer_protection' and (h.get('ref')==card.get('ref') or r.get('team'))
                   for h in other['active']+other['bench'] for r in rules(h))

def candy_basic(own):
    return any(c['name']==BASIC and not c.get('firstTurnPlayed',True) for c in own['active']+own['bench']) and not own.get('turnFlags',{}).get('firstPersonalTurn')

def bench_liability(card,own,other):
    """Only decline late incidental low-HP bodies; keep attackers/support roles."""
    if other.get('prize_count')!=1 or card.get('name') not in ('Budew','Snorunt'):return False
    if not (shadow(own) or any(c['name']=='Bloodmoon Ursaluna ex' for c in own['hand']+own['active']+own['bench'])):return False
    field=other['active']+other['bench']
    shrouds=sum(c.get('name')=='Froslass' for c in field+own['active']+own['bench'])
    monkeys=sum(c['name']=='Munkidori' and 'DARK' in c.get('energy',[]) and c.get('hp',0)>shrouds*10 for c in field)
    burst=min(3*monkeys,sum(counters(c) for c in field))*10
    return 0<card.get('hp',9999)<=burst

def decide(dto, *, entries, environment):
    started=time.perf_counter();cmd,detail=previous.decide(dto,entries=entries,environment=environment)
    if detail.get('route')!=previous.VERSION:return cmd,detail
    own,other=dto['observation']['self'],dto['observation']['opponent'];d=dto['decision'];reason='unchanged-v1'
    if d['kind']=='selection' and not d.get('hidden'):
        cs=d['candidates'];chosen=[c for c in cs if c['ref'] in cmd['choice'].get('selectedRefs',[])]
        if d.get('source') in GUSTS and chosen and shadow(own) and all(c.get('side')=='opponent' for c in cs):
            ranked=sorted((c for c in cs if gust_reachable(candidate_card(c),other)),key=lambda c:exchange(own,other,candidate_card(c)),reverse=True)
            if ranked and exchange(own,other,candidate_card(ranked[0]))>exchange(own,other,candidate_card(chosen[0])):
                cmd={**cmd,'choice':{'selectedRefs':[ranked[0]['ref']]}};reason='gust-front-and-bench-prizes-together'
        elif d.get('source')==MAIN and chosen and all(c.get('side')=='opponent' for c in cs):
            def prizes(c):return c['card'].get('prize',1) if 0<c['card'].get('hp',9999)<=bench_hit(candidate_card(c),own,other) else 0
            best=max(cs,key=prizes)
            if prizes(best)>prizes(chosen[0]):cmd={**cmd,'choice':{'selectedRefs':[best['ref']]}};reason='shadow-bullet-finish-bench-prize'
        elif d.get('source')=='Buddy-Buddy Poffin' and chosen:
            safe=[c['ref'] for c in chosen if not bench_liability(c['card'],own,other)]
            if len(safe)<len(chosen) and len(safe)>=d['min']:
                cmd={**cmd,'choice':{'selectedRefs':safe}};reason='decline-last-prize-counter-fodder'
    elif d['kind']=='options':
        options=d['options'];chosen=next(o for o in options if o['id']==cmd['choice']['optionId'])
        hand=[c['name'] for c in own['hand']]
        # A guaranteed legal Candy option outranks the detour. Without Candy,
        # Arven is a search opportunity, not a claim that a hidden copy exists.
        detour=(chosen['actionType']=='EvolvePokemonAction' and chosen.get('source')==MID) or (chosen['actionType']=='UseItemAction' and chosen.get('source')=='Night Stretcher')
        if detour and MAIN in hand and candy_basic(own) and not own.get('turnFlags',{}).get('itemsBlocked'):
            candy=next((o for o in options if o['actionType']=='UseItemAction' and o.get('source')=='Rare Candy'),None)
            arven=next((o for o in options if o['actionType']=='UseSupporterAction' and o.get('source')=='Arven'),None)
            discarded=sum(c['name']=='Rare Candy' for c in own['discard'])
            next_step=candy or (arven if discarded<2 and 'Rare Candy' not in hand else None)
            if next_step:chosen=next_step;cmd={**cmd,'choice':{'optionId':chosen['id']}};reason='candy-before-morgrem-detour'
        if chosen['actionType']=='PlayPokemonAction':
            card=next((c for c in own['hand'] if c.get('ref')==chosen.get('sourceRef')), {})
            if bench_liability(card,own,other):
                filtered=[o for o in options if not(o['actionType']=='PlayPokemonAction' and o.get('source')==card.get('name'))]
                cmd,_=previous.decide({**dto,'decision':{**d,'options':filtered}},entries=entries,environment=environment)
                chosen=next(o for o in options if o['id']==cmd['choice']['optionId']);reason='hold-last-prize-counter-fodder'
        supporter=iono_before_ko(dto,chosen)
        if supporter:cmd={**cmd,'choice':{'optionId':supporter['id']}};reason='draw-before-nonterminal-prize-attack'
    assert legal(dto,cmd)
    return cmd,{**detail,'route':VERSION,'reason':reason,'aiMilliseconds':(time.perf_counter()-started)*1000}

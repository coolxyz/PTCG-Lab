"""Bounded current-turn plans: evolution, one Munkidori, gust and Shadow Bullet.

Only public projections and engine-provided legal next actions. No hidden draws,
Punk Up deck-energy guarantee, or cooperative opponent promotion is assumed.
"""
import time
from apps.api.expert_policies import grimmsnarl_legacy_v1 as previous
from apps.api.expert_policies import grimmsnarl_prize as parent
from packages.battle.h2_v4.visible_math import counters
from packages.battle.h2_v4.tempo import estimate
from packages.simulation.a2 import legal

PROFILE=previous.PROFILE
VERSION='h2-grimmsnarl-turn-plan-v6'
MAIN,MID,BASIC=parent.MAIN,parent.MID,parent.BASIC

def moving_units(own):
    # On this exact list every attack is independent of own damage counters.
    # The donor stage explicitly enforces this maximum; number stage takes max.
    return min(3,max((counters(c) for c in own['active']+own['bench']),default=0))

def gust_names(own):
    names={c['name'] for c in own['hand']};flags=own.get('turnFlags',{})
    return (["Counter Catcher"] if 'Counter Catcher' in names and not flags.get('itemsBlocked') else []) + (["Boss's Orders"] if "Boss's Orders" in names and not flags.get('supporterPlayed') else [])

def attack_plan(own,other,gusts=None):
    if not parent.shadow(own) or not other['active']:return {'score':0,'front':None,'gust':None}
    front=other['active'][0];cap=own['prize_count']
    probability=.5 if own['active'][0].get('specialCondition')=='CONFUSED' else 1
    best={'score':probability*min(cap,parent.exchange(own,other,front)),'front':front['ref'],'gust':None}
    for source in (gust_names(own) if gusts is None else gusts):
        if source=='Counter Catcher' and own['prize_count']<=other['prize_count']:continue
        for c in other['bench']:
            if not parent.gust_reachable(c,other):continue
            score=probability*min(cap,parent.exchange(own,other,c))
            if score>best['score']:best={'score':score,'front':c['ref'],'gust':source}
    return best

def transfer_score(own,other,target,units,gusts=None):
    if units<=0 or any(r.get('kind')=='ability_protection' for r in parent.rules(target)):return -1
    amount=units*10;ko=target.get('hp',9999)<=amount
    gain=target.get('prize',1) if ko else 0
    if gain>=own['prize_count']:return own['prize_count']
    after_own={**own,'prize_count':own['prize_count']-gain}
    remaining=[({**c,'hp':c['hp']-amount} if c['ref']==target['ref'] else c) for c in other['active']+other['bench'] if not(ko and c['ref']==target['ref'])]
    if not remaining:return min(own['prize_count'],gain)
    if ko and target['ref']==other['active'][0]['ref']:
        # Opponent chooses the replacement. Take the worst legal public branch.
        scores=[attack_plan(after_own,{**other,'active':[c],'bench':[b for b in remaining if b['ref']!=c['ref']]},gusts)['score'] for c in remaining]
        return min(own['prize_count'],gain+min(scores))
    active_ref=other['active'][0]['ref']
    after={**other,'active':[c for c in remaining if c['ref']==active_ref],'bench':[c for c in remaining if c['ref']!=active_ref]}
    return min(own['prize_count'],gain+attack_plan(after_own,after,gusts)['score'])

def best_transfer(own,other,gusts=None):
    n=moving_units(own)
    values=[(transfer_score(own,other,c,n,gusts),c['ref']) for c in other['active']+other['bench']]
    return max(values,default=(0,None))

def front_hit(own,other):
    a=parent.shadow(own);front=(other['active'] or [{}])[0]
    if not a or parent.protected(front,other,own['active'][0],False):return 0
    return parent.damage(a,own['active'][0],front,own,other)

def evolved_active(own,option):
    active=(own['active'] or [{}])[0]
    if option.get('actionType')=='EvolvePokemonAction':
        if option.get('source')!=MAIN or option.get('targetRef')!=active.get('ref'):return None
    elif option.get('actionType')=='UseItemAction' and option.get('source')=='Rare Candy':
        if active.get('name')!=BASIC or active.get('firstTurnPlayed',True) or own.get('turnFlags',{}).get('firstPersonalTurn'):return None
    else:return None
    card=next((c for c in own['hand'] if c['name']==MAIN),None)
    if not card or active.get('energy',[]).count('DARK')<2:return None
    main={**card,'ref':active['ref'],'hp':320-counters(active)*10,'energy':active['energy'],'tool':active.get('tool',[]),'firstTurnPlayed':False}
    return {**own,'active':[main]}

def decide(dto,*,entries,environment):
    start=time.perf_counter();cmd,detail=parent.decide(dto,entries=entries,environment=environment)
    if detail.get('route')!=parent.VERSION:return cmd,detail
    own,other=dto['observation']['self'],dto['observation']['opponent'];d=dto['decision'];reason='inherited-v5'
    if d['kind']=='options':
        options=d['options'];chosen=next(o for o in options if o['id']==cmd['choice']['optionId'])
        active=(own['active'] or [{}])[0];enemy=(other['active'] or [{}])[0]
        terminal=active.get('specialCondition')!='CONFUSED' and chosen['actionType']=='AttackAction' and estimate(chosen.get('attack',{}),active,own,other)>=enemy.get('hp',9999)>0 and (enemy.get('prize',1)>=own['prize_count'] or not other['bench'])
        if not terminal:
            # Legal active evolution preserves the current KO while adding HP,
            # acceleration opportunity and bench damage. Require existing energy.
            for o in options:
                evolved=evolved_active(own,o)
                if not evolved or enemy.get('name')=='Mimikyu':continue
                before=estimate(chosen.get('attack',{}),active,own,other) if chosen['actionType']=='AttackAction' else 0
                after=attack_plan(evolved,other,[])['score']
                if chosen['actionType']=='AttackAction' and before>=enemy.get('hp',9999)>0 and front_hit(evolved,other)>=enemy.get('hp',9999) and after>=enemy.get('prize',1):
                    chosen=o;cmd={**cmd,'choice':{'optionId':o['id']}};reason='evolve-active-preserve-ko-and-attack-route';break
            if parent.shadow(own):
                legal_gusts=[o.get('source') for o in options if o['actionType'] in ('UseItemAction','UseSupporterAction') and o.get('source') in parent.GUSTS]
                direct=attack_plan(own,other,[]);gust=attack_plan(own,other,legal_gusts)
                monkey=next((o for o in options if o['actionType']=='UseAbilityAction' and o.get('source')=='Munkidori'),None)
                transfer=best_transfer(own,other,legal_gusts)[0] if monkey else -1
                if transfer>max(direct['score'],gust['score']):
                    chosen=monkey;cmd={**cmd,'choice':{'optionId':monkey['id']}};reason='start-transfer-gust-attack-plan'
                elif gust['score']>direct['score'] and not(chosen['actionType']=='UseAbilityAction' and chosen.get('source')=='Munkidori' and transfer>=gust['score']):
                    step=next(o for o in options if o.get('source')==gust['gust'] and o['actionType'] in ('UseItemAction','UseSupporterAction'))
                    chosen=step;cmd={**cmd,'choice':{'optionId':step['id']}};reason='start-gust-before-draw-or-direct-attack'
                # Existing legal Darkness attachment can complete a current-turn
                # transfer route. Do not budget unknown future attachments.
                attached_value=max(direct['score'],gust['score'],transfer)
                for o in options:
                    if o['actionType']!='AttachEnergyAction' or o.get('source')!='Darkness Energy':continue
                    target=next((c for c in own['active']+own['bench'] if c['ref']==o.get('targetRef')), {})
                    if target.get('name')!='Munkidori' or target.get('abilityUsed') or 'DARK' in target.get('energy',[]):continue
                    projected={**own,'bench':[({**c,'energy':c.get('energy',[])+['DARK']} if c['ref']==target['ref'] else c) for c in own['bench']]}
                    if target in own['active']:continue
                    score=best_transfer(projected,other,legal_gusts)[0]
                    if score>attached_value:
                        attached_value=score;chosen=o;cmd={**cmd,'choice':{'optionId':o['id']}};reason='attach-darkness-starts-transfer-attack-plan'
    elif d['kind']=='selection' and not d.get('hidden') and d['candidates']:
        cs=d['candidates'];selected=[c for c in cs if c['ref'] in cmd['choice'].get('selectedRefs',[])]
        if d.get('source')=='Munkidori' and all(c.get('side')=='self' for c in cs):
            cap=max(min(3,counters(c['card'])) for c in cs)
            if selected and min(3,counters(selected[0]['card']))<cap:
                donor=next(c for c in cs if min(3,counters(c['card']))==cap)
                cmd={**cmd,'choice':{'selectedRefs':[donor['ref']]}};reason='transfer-donor-funds-public-plan'
        elif d.get('source')=='Munkidori' and all(c.get('side')=='opponent' for c in cs) and parent.shadow(own):
            n=moving_units(own)
            ranked=sorted(cs,key=lambda c:transfer_score(own,other,parent.candidate_card(c),n),reverse=True)
            if selected and transfer_score(own,other,parent.candidate_card(ranked[0]),n)>transfer_score(own,other,parent.candidate_card(selected[0]),n):
                cmd={**cmd,'choice':{'selectedRefs':[ranked[0]['ref']]}};reason='transfer-target-completes-prize-chain'
        elif d.get('source') in parent.GUSTS and all(c.get('side')=='opponent' for c in cs) and parent.shadow(own):
            ready=any(c['name']=='Munkidori' and 'DARK' in c.get('energy',[]) and not c.get('abilityUsed') for c in own['active']+own['bench'])
            if ready and moving_units(own)>0:
                def score(c):
                    front=parent.candidate_card(c);team={**other,'active':[front],'bench':[b for b in other['active']+other['bench'] if b['ref']!=front['ref']]}
                    return max(attack_plan(own,team,[])['score'],best_transfer(own,team,[])[0]) if parent.gust_reachable(front,other) else -1
                best=max(cs,key=score)
                if selected and score(best)>score(selected[0]):cmd={**cmd,'choice':{'selectedRefs':[best['ref']]}};reason='gust-target-includes-following-transfer'
        elif d.get('source')=='Rare Candy' and all(c.get('side')=='self' and c.get('boardRef') for c in cs):
            target=next((c for c in cs if c['boardRef']==(own['active'] or [{}])[0].get('ref') and c['card'].get('energy',[]).count('DARK')>=2),None)
            if target and selected and selected[0]['ref']!=target['ref']:
                cmd={**cmd,'choice':{'selectedRefs':[target['ref']]}};reason='candy-evolves-funded-active-plan'
    assert legal(dto,cmd)
    return cmd,{**detail,'route':VERSION,'reason':reason,'aiMilliseconds':(time.perf_counter()-start)*1000}

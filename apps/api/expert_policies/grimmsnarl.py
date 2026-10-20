"""Preserve executable preparation; compare public two-attack endgame routes.

The return model is conditional on the visible board, not a guarantee against
unknown hands, draws or evolution. Only actual legal first actions are emitted.
"""
import time
from apps.api.expert_policies import grimmsnarl_turn as parent
from packages.battle.h2_v4.policy import deficit
from packages.battle.h2_v4.tempo import estimate
from packages.simulation.a2 import legal

previous=parent.previous
PROFILE=previous.PROFILE
VERSION='h2-grimmsnarl-exchange-plan-v7'
MAIN,MID,BASIC=parent.MAIN,parent.MID,parent.BASIC
facts=parent.parent

def field(team):return team['active']+team['bench']

def bench_choice(own,other):
    """Exactly the inherited v5 bench-target ranking, including its tie order."""
    cs=other['bench']
    if not cs:return None
    baseline=max(cs,key=lambda c:-(c.get('hp') or 999)+(50 if 'ex' in c.get('name','') else 0))
    def prize(c):return c.get('prize',1) if 0<c.get('hp',9999)<=facts.bench_hit(c,own,other) else 0
    best=max(cs,key=prize)
    return best if prize(best)>prize(baseline) else baseline

def after_shadow(own,other,front):
    attack=facts.shadow(own);a=own['active'][0]
    team={**other,'active':[front],'bench':[c for c in field(other) if c['ref']!=front['ref']]}
    amount=0 if facts.protected(front,team,a,False) else facts.damage(attack,a,front,own,team)
    bench=bench_choice(own,team);points=0;left=[]
    for c in field(team):
        hit=amount if c['ref']==front['ref'] else (facts.bench_hit(c,own,team) if bench and c['ref']==bench['ref'] else 0)
        hp=c['hp']-hit
        if hp<=0:points+=c.get('prize',1)
        else:left.append({**c,'hp':hp})
    return min(own['prize_count'],points),left

def checkup_loss(card,teams):
    shrouds=sum(c['name']=='Froslass' for t in teams for c in field(t))
    return shrouds*10 if card.get('abilities') and card['name']!='Froslass' else 0

def reply_finish(own,other,points,left,used_gust):
    """Worst visible promotion/pivot and funded attack; no future energy assumed.

    Omit unknown draw/evolution, attachment, copying effect extras and new gusts.
    This is a conditional route estimate, never exposed as a certain win.
    """
    remaining=own['prize_count']-points
    if not remaining or not left:return own['prize_count']
    hand=list(own['hand'])
    used=next((i for i,c in enumerate(hand) if c['name']==used_gust),None)
    if used is not None:hand.pop(used)
    after_own={**own,'prize_count':remaining,'hand':hand,
               'turnFlags':{**own.get('turnFlags',{}),'supporterPlayed':False}}
    after_other={**other,'active':[left[0]],'bench':left[1:],'hand':[]}
    # End-of-turn checkup can change both the race and the Catcher condition.
    def tick(team):
        return [{**c,'hp':c['hp']-checkup_loss(c,(after_own,after_other))} for c in field(team)]
    ours=tick(after_own);theirs=tick(after_other)
    lost=sum(c.get('prize',1) for c in ours if c['hp']<=0)
    gained=sum(c.get('prize',1) for c in theirs if c['hp']<=0)
    if gained>=remaining:return own['prize_count']
    if lost>=other['prize_count'] or ours[0]['hp']<=0:return points
    remaining-=gained
    after_own={**after_own,'prize_count':remaining,'active':[ours[0]],'bench':[c for c in ours[1:] if c['hp']>0]}
    theirs=[c for c in theirs if c['hp']>0]
    if not theirs:return own['prize_count']
    after_other={**after_other,'prize_count':other['prize_count']-lost,'active':[theirs[0]],'bench':theirs[1:]}
    # Each currently funded monkey can heal/move at most three existing counters.
    burst=min(sum(facts.counters(c) for c in theirs),3*sum(c['name']=='Munkidori' and 'DARK' in c.get('energy',[]) for c in theirs))*10
    values=[]
    for c in theirs:
        enemy={**after_other,'active':[c],'bench':[b for b in theirs if b['ref']!=c['ref']]}
        replies=[None]+[a for a in c.get('attacks',[]) if not c.get('attackBlocked') and a['name'] not in c.get('limitedAttacks',[]) and deficit(a.get('cost',[]),c.get('energy',[]))==0]
        for a in replies:
            amount=estimate(a,c,enemy,after_own) if a else 0
            if a and facts.protected(ours[0],after_own,c,False):amount=0
            hp=ours[0]['hp']-amount-burst
            hp-=checkup_loss(ours[0],(after_own,enemy))
            if hp<=0:values.append(points+gained);continue
            # Conservative double use of the upper bound: grant the reply both
            # its maximum transfer pressure and healing on the responding card.
            healed={**c,'hp':c['hp']+min(burst,facts.counters(c)*10)}
            enemy={**enemy,'active':[healed]}
            future_own={**after_own,'active':[{**ours[0],'hp':hp}]}
            probability=.5 if a and ('confused' in a.get('text','').lower() or a.get('name')=='Mind Bend') else 1
            future=parent.attack_plan(future_own,enemy)['score']
            values.append(points+gained+(remaining*probability if future>=remaining else 0))
    return min(values,default=points+gained)

def attack_routes(own,other,gusts):
    if not facts.shadow(own) or not other['active']:return {'value':0,'now':0,'front':None,'gust':None}
    options=[(other['active'][0],None)]+[(c,g) for g in gusts for c in other['bench'] if facts.gust_reachable(c,other) and (g!='Counter Catcher' or own['prize_count']>other['prize_count'])]
    routes=[]
    for front,gust in options:
        points,left=after_shadow(own,other,front)
        probability=.5 if own['active'][0].get('specialCondition')=='CONFUSED' else 1
        now=points*probability;value=now
        # Use the extended model only for the last three prizes. Require a
        # publicly funded return route that could actually finish, not a bonus
        # for speculative damage/unknown future development everywhere.
        if probability==1 and 0<points<own['prize_count']<=3:
            finish=reply_finish(own,other,points,left,gust)
            if finish>points:value=finish
        routes.append({'value':value,'now':now,'front':front['ref'],'gust':gust})
    return max(routes,key=lambda r:(r['value'],r['now'],r['gust'] is None))

def prep_action(dto,chosen,parent_chosen):
    """Concrete non-attacking work that a gust-generated KO must not truncate."""
    own,other=dto['observation']['self'],dto['observation']['opponent'];options=dto['decision']['options']
    if not facts.shadow(own):return None
    gusts=[o['source'] for o in options if o['actionType'] in ('UseItemAction','UseSupporterAction') and o.get('source') in facts.GUSTS]
    best=parent.attack_plan(own,other,gusts)['score']
    # Preserve inherited preparation that does not strip the attack's energy or
    # switch the attacker. Supporters compete with Boss, so only keep actual
    # inherited choices; never invent a productive search result.
    kind=parent_chosen['actionType'];name=parent_chosen.get('source')
    safe=kind in ('PlayPokemonAction','EvolvePokemonAction','UseStadiumAction')
    safe |= kind=='UseItemAction' and name in ('Poké Pad','Buddy-Buddy Poffin','Night Stretcher','Super Rod','Rare Candy','Ultra Ball','Secret Box')
    safe |= kind=='UseSupporterAction' and name in ('Arven','Iono',"Lillie's Determination")
    if kind=='UseSupporterAction' and name in ('Iono',"Lillie's Determination"):
        # A shuffle discards the executable gust route. Use an item gust first,
        # then re-evaluate drawing from its actual resulting hand/board.
        safe=False
    if safe and kind=='UseItemAction' and name in ('Ultra Ball','Secret Box'):
        count=2 if name=='Ultra Ball' else 3
        safe=sum(c['name'] not in facts.GUSTS and c['ref']!=parent_chosen.get('sourceRef') for c in own['hand'])>=count
    if safe:return parent_chosen
    # After gusting, the old KO score would now suppress all the same work.
    # Complete only material, legal roles, not a blanket bench or draw quota.
    for o in options:
        if o['actionType']=='EvolvePokemonAction' and o.get('targetRef')!=(own['active'] or [{}])[0].get('ref'):
            target=next((c for c in own['bench'] if c['ref']==o.get('targetRef')), {})
            if o.get('source') in (MAIN,MID) and target.get('energy',[]).count('DARK')>=2:return o
    n=parent.moving_units(own)
    if n:
        for o in options:
            if o['actionType']=='UseAbilityAction' and o.get('source')=='Munkidori' and parent.best_transfer(own,other,gusts)[0]>=best:return o
        for o in options:
            if o['actionType']=='AttachEnergyAction' and o.get('source')=='Darkness Energy':
                target=next((c for c in own['bench'] if c['ref']==o.get('targetRef')), {})
                if target.get('name')=='Munkidori' and not target.get('abilityUsed') and 'DARK' not in target.get('energy',[]):
                    projected={**own,'bench':[({**c,'energy':c.get('energy',[])+['DARK']} if c['ref']==target['ref'] else c) for c in own['bench']]}
                    if parent.best_transfer(projected,other,gusts)[0]>=best:return o
    return None

def decide(dto,*,entries,environment):
    start=time.perf_counter();cmd,detail=parent.decide(dto,entries=entries,environment=environment)
    if detail.get('route')!=parent.VERSION:return cmd,detail
    own,other=dto['observation']['self'],dto['observation']['opponent'];d=dto['decision'];reason='inherited-v6'
    if d['kind']=='options' and facts.shadow(own):
        options=d['options'];selected=next(o for o in options if o['id']==cmd['choice']['optionId'])
        gusts=[o['source'] for o in options if o['actionType'] in ('UseItemAction','UseSupporterAction') and o.get('source') in facts.GUSTS]
        best=attack_routes(own,other,gusts);direct=attack_routes(own,other,[])
        terminal=own['active'][0].get('specialCondition')!='CONFUSED' and (best['now']>=own['prize_count'] or (not other['bench'] and parent.front_hit(own,other)>=other['active'][0]['hp']))
        tactical=selected['actionType']=='AttackAction' or selected.get('source') in facts.GUSTS
        if not terminal and tactical:
            basecmd,_=facts.decide(dto,entries=entries,environment=environment)
            basechosen=next(o for o in options if o['id']==basecmd['choice']['optionId'])
            prep=prep_action(dto,selected,basechosen)
            if prep:selected=prep;reason='preserve-preparation-before-gust-generated-ko'
            elif best['value']>direct['value'] and best['gust']:
                selected=next(o for o in options if o.get('source')==best['gust'] and o['actionType'] in ('UseItemAction','UseSupporterAction'));reason='gust-evaluates-public-return-exchange'
            elif detail.get('reason')=='start-gust-before-draw-or-direct-attack':
                attack=next((o for o in options if o['actionType']=='AttackAction' and o.get('attack',{}).get('name')=='Shadow Bullet'),None)
                if attack:selected=attack;reason='direct-attack-preserves-return-win-route'
            cmd={**cmd,'choice':{'optionId':selected['id']}}
    elif d['kind']=='selection' and d.get('source') in facts.GUSTS and not d.get('hidden') and facts.shadow(own) and all(c.get('side')=='opponent' for c in d['candidates']):
        # Only alter inherited immediate targeting if the two-attack model
        # strictly improves value. Keep v6 transfer-aware target combinations.
        chosen=next((c for c in d['candidates'] if c['ref'] in cmd['choice'].get('selectedRefs',[])),None)
        def value(c):
            front=facts.candidate_card(c);team={**other,'active':[front],'bench':[b for b in field(other) if b['ref']!=front['ref']]}
            return attack_routes(own,team,[])['value'] if facts.gust_reachable(front,other) else -1
        if chosen:
            best=max(d['candidates'],key=value)
            if value(best)>value(chosen):cmd={**cmd,'choice':{'selectedRefs':[best['ref']]}};reason='gust-target-includes-public-return-exchange'
    assert legal(dto,cmd)
    return cmd,{**detail,'route':VERSION,'reason':reason,'aiMilliseconds':(time.perf_counter()-start)*1000}

# Helper exports used by the four unchanged experts.
deck_hash=previous.deck_hash
environment_hash=previous.environment_hash
plan=previous.plan
resource=previous.resource
energy_value=previous.energy_value
expert=previous.expert
LINE=previous.LINE

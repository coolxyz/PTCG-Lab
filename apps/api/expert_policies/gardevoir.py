"""Exact-list basic Gardevoir policy. Public observations, no private engine state."""
from collections import Counter
from pathlib import Path
import json,time
from apps.api.expert_policies.grimmsnarl import deck_hash,environment_hash
from apps.api.expert_policies.energy import energy_priority
from packages.battle import h2
from packages.battle.h2_v4 import policy as base
from packages.battle.h2_v4.tempo import attack_value
from packages.battle.h2_v4.visible_math import damage,counters,mechanic
from packages.simulation.a2 import legal
PROFILE=json.loads((Path(__file__).parent/'gardevoir-profile.json').read_text())
VERSION='h2-gardevoir-basic-v5'
LINE=('Ralts','Kirlia','Gardevoir ex')
ATTACKERS=('Drifloon','Scream Tail',"Lillie's Clefairy ex",'Gardevoir ex')

def plan(dto):
    own=dto['observation']['self'];field=own['active']+own['bench'];names=Counter(c['name'] for c in field);hand=Counter(c['name'] for c in own['hand']);needs=[]
    if sum(names[n] for n in LINE)<2:needs.append('Ralts')
    if names['Ralts'] and not names['Gardevoir ex']:needs.insert(0,'Kirlia')
    if names['Kirlia'] or (names['Ralts'] and hand['Rare Candy']):needs.insert(0,'Gardevoir ex')
    if not names['Drifloon']:needs.append('Drifloon')
    if not names['Munkidori']:needs.append('Munkidori')
    return {'stateVersion':dto['stateVersion'],'goal':'establish-embrace-and-cycle-attackers','needs':list(dict.fromkeys(needs)),'field':dict(names),'hand':dict(hand),'discardPsychic':sum(c.get('name')=='Psychic Energy' for c in own['discard'])}

def attack_score(a,c,own,other):
    if a.get('name')=='Roaring Scream':
        amount=counters(c)*20
        targets=other['active']+other['bench']
        return max(((400 if amount>=t.get('hp',9999)>0 else 65)+min(amount,400)/8 for t in targets),default=0)
    return attack_value(a,c,own,other)

def ready(c,own,other):
    if c.get('attackBlocked'):return -100
    return max((attack_score(a,c,own,other) for a in c.get('attacks',[]) if base.deficit(a.get('cost',[]),c.get('energy',[]))==0 and a.get('name') not in c.get('limitedAttacks',[])),default=-100)

def embrace_plan(c,own,other):
    """Small public budget: stop at KO, avoid <=20 HP for speculative extra damage."""
    if c.get('name') not in ATTACKERS or c.get('cardType')!='PSYCHIC' or c.get('attackBlocked') or c.get('hp',0)<=20:return None
    available=sum(x.get('name')=='Psychic Energy' for x in own['discard'])
    before=ready(c,own,other)
    if before>=400:return None
    best=None
    for n in range(1,min(available,(c['hp']-1)//20,6)+1):
        after={**c,'hp':c['hp']-20*n,'energy':c.get('energy',[])+['PSYCHIC']*n}
        value=ready(after,own,other)
        if value<=max(0,before):continue
        if after['hp']<=20 and value<400:continue
        # Prefer one-prize attackers and current active; minimize damage once KO is ready.
        utility=value+ (35 if c.get('prize',1)==1 else 0)+(25 if c in own['active'] else 0)-n*8
        if best is None or utility>best['value']:best={'value':utility,'attachments':n,'remainingHp':after['hp'],'attackValue':value,'target':c.get('ref')}
        if value>=400:break
    return best

def turn_plan(own,other,can_embrace=None):
    """Joint visible resource budget: hand attachment, retreat fuel and attacker.

    Enumerate only this turn's available energy. No assumed draw, evolution or
    switch card. Every chosen allocation ends in an attack and survives Embrace.
    """
    active=(own['active'] or [{}])[0]
    if not active:return None
    if can_embrace is None:
        can_embrace=any(c.get('name')=='Gardevoir ex' for c in own['active']+own['bench'])
    fuel=sum(x.get('name')=='Psychic Energy' for x in own['discard']) if can_embrace else 0
    sources=[] if own.get('turnFlags',{}).get('energyPlayed',False) else [c for c in own['hand'] if c.get('superType')=='ENERGY' and (c.get('provides') or c.get('cardType'))]
    current=ready(active,own,other);best=None
    for attacker in own['active']+own['bench']:
        bench=attacker not in own['active']
        if bench and (active.get('retreatBlocked') or own.get('turnFlags',{}).get('retreated')):continue
        manual=[(None,None)]+[(e,t) for e in sources for t in ([active,attacker] if bench else [attacker])]
        for energy,target in manual:
            provides=(energy.get('provides') or [energy['cardType']]) if energy else []
            a={**active,'energy':active.get('energy',[])+(provides if target is active else [])}
            c={**attacker,'energy':attacker.get('energy',[])+(provides if target is attacker else [])}
            retreat=max(0,len(a.get('retreatCost',[]))-len(a['energy'])) if bench else 0
            if retreat and (a.get('cardType')!='PSYCHIC' or a.get('hp',0)<=20*retreat or retreat>fuel):continue
            limit=min(fuel-retreat,(c.get('hp',0)-1)//20,6) if c.get('name') in ATTACKERS and c.get('cardType')=='PSYCHIC' else 0
            for n in range(max(0,limit)+1):
                projected={**c,'hp':c.get('hp',0)-20*n,'energy':c['energy']+['PSYCHIC']*n}
                value=ready(projected,own,other)
                if value<=0 or (n and projected['hp']<=20 and value<400):continue
                if bench and value<=max(0,current)+20:continue
                if not bench and (n or energy) and value<=max(0,current):continue
                utility=value+(35 if c.get('prize',1)==1 else 0)+(25 if not bench else 0)-8*(n+retreat)-12*bool(energy)
                candidate={'value':utility,'attackValue':value,'attacker':attacker.get('ref'),'manualSource':energy.get('ref') if energy else None,'manualTarget':target.get('ref') if target else None,'embraceTarget':active.get('ref') if retreat else attacker.get('ref') if n else None,'retreatAttachments':retreat,'attachments':n,'switch':bench,'remainingHp':projected['hp']}
                if best is None or utility>best['value']:best=candidate
    return best

def embrace_target(own,other):
    p=turn_plan(own,other)
    return {**p,'target':p['embraceTarget']} if p and p['embraceTarget'] else None

def manual_energy_value(source,target,own,other,sequence):
    # Reserve the hand attachment only when the current attack actually needs it.
    # An already funded attack must not freeze next-turn or support development.
    if sequence and sequence['manualSource']:
        if source.get('ref')==sequence['manualSource'] and target.get('ref')==sequence['manualTarget']:
            return 550+sequence['attackValue']/10
        return -120
    if source.get('name')=='Darkness Energy' and target.get('name')=='Munkidori' and 'DARK' not in target.get('energy',[]):
        return 260 if any(counters(c)>0 for c in own['active']+own['bench']) else 160
    return energy_priority(source,target,own,other)

def resource(c,p,own,other):
    n=c.get('name')
    if n in p['needs']:return 290-p['needs'].index(n)*25 if not p['hand'].get(n) else 30
    if n=='Rare Candy' and p['field'].get('Ralts') and p['hand'].get('Gardevoir ex'):return 280
    if n=='Bravery Charm' and any(p['field'].get(x) for x in ('Drifloon','Scream Tail')):return 200
    if n=='Psychic Energy':
        # Discard is the engine's fuel, not automatically a card to recover to hand.
        if p['field'].get('Gardevoir ex') and p['discardPsychic']>=2:return 15
        return 100 if not p['hand'].get(n) and not own.get('turnFlags',{}).get('energyPlayed',True) else 25
    if n=='Darkness Energy' and p['field'].get('Munkidori') and not p['hand'].get(n):return 150
    if n in ('Buddy-Buddy Poffin','Poké Pad','Ultra Ball','Arven') and not p['field'].get('Gardevoir ex'):return 200
    return base.utility(c,own)

def decide(dto,*,entries,environment):
    if dto['observation']['opponent']['hand'] is not None:raise ValueError('HIDDEN_HAND_EXPOSED')
    original,detail=h2.decide(dto)
    if deck_hash(entries)!=PROFILE['deckHash'] or environment!=PROFILE['environmentHash']:return original,{**detail,'route':'frozen-h2'}
    start=time.perf_counter();own=dto['observation']['self'];other=dto['observation']['opponent'];field=own['active']+own['bench'];active=(own['active'] or [{}])[0];d=dto['decision'];p=plan(dto);cmd=original;reason='shared'
    can_embrace=any(o['actionType']=='UseAbilityAction' and o.get('source')=='Gardevoir ex' for o in d.get('options',[])) if d['kind']=='options' else None
    sequence=turn_plan(own,other,can_embrace)
    if d['kind']=='selection':
        cs=d['candidates'];cost=d.get('zones')==['CardPosition.HAND'] and dto.get('phase')=='playing'
        if cs and not d.get('hidden'):
            ordered=None;count=d['min'] if cost else min(d['max'],len(cs))
            if d.get('source')=='Gardevoir ex' and all(x.get('side')=='self' for x in cs):
                target={'target':sequence['embraceTarget']} if sequence else None
                ordered=sorted(cs,key=lambda x:x.get('boardRef')==(target or {}).get('target'),reverse=True);reason='embrace-executable-attacker'
            elif d.get('source')=='Scream Tail' and all(x.get('side')=='opponent' for x in cs):
                amount=counters(active)*20
                ordered=sorted(cs,key=lambda x:(1000*x['card'].get('prize',1) if 0<x['card'].get('hp',999)<=amount else -x['card'].get('hp',999)),reverse=True);reason='snipe-prize-target'
            elif dto.get('phase')=='active' or (d.get('zones')==['CardPosition.BENCH'] and all(x.get('side')=='self' for x in cs)):
                ordered=sorted(cs,key=lambda x:(1000 if sequence and x.get('boardRef')==sequence['attacker'] else 0)+ready(x['card'],own,other)+(20 if x['card'].get('prize',1)==1 else 0),reverse=True);reason='promote-funded-attacker'
            elif d.get('source')=='Bravery Charm' and all(x.get('side')=='self' for x in cs):
                ordered=sorted(cs,key=lambda x:100 if x['card'].get('name')=='Drifloon' else 90 if x['card'].get('name')=='Scream Tail' else 0,reverse=True);reason='expand-attack-damage-budget'
            elif cost:
                keep=set()
                if sequence and sequence['manualSource']:
                    source=next((x for x in own['hand'] if x.get('ref')==sequence['manualSource']),{})
                    match=next((x for x in cs if x['card'].get('name')==source.get('name')),None)
                    if match:keep.add(match['ref'])
                for n in p['needs']+(['Rare Candy'] if p['hand'].get('Gardevoir ex') else []):
                    match=next((x for x in cs if x['card'].get('name')==n),None)
                    if match:keep.add(match['ref'])
                ordered=sorted(cs,key=lambda x:(-100 if x['card'].get('name')=='Psychic Energy' and (p['field'].get('Gardevoir ex') or p['hand'].get('Gardevoir ex')) and p['discardPsychic']<4 else resource(x['card'],p,own,other))+(300 if x['ref'] in keep else 0));reason='discard-energy-preserve-evolution'
            elif not any(x.get('side') or x.get('card',{}).get('kind')=='attack' for x in cs) and dto.get('phase')=='playing':
                ordered=sorted(cs,key=lambda x:resource(x['card'],p,own,other),reverse=True);reason='search-recover-engine'
                if d.get('source')=='Super Rod':
                    useful=[x for x in ordered if x['card'].get('superType')!='ENERGY'];rest=[x for x in ordered if x not in useful]
                    ordered=useful+rest;count=max(d['min'],min(count,len(useful)))
                elif count>1:
                    unique=[];rest=[];seen=set()
                    for x in ordered:
                        n=x['card'].get('name');(rest if n in seen else unique).append(x);seen.add(n)
                    ordered=unique+rest
            if ordered is not None:cmd={**cmd,'choice':{'selectedRefs':[x['ref'] for x in ordered[:count]]}}
    else:
        def score(o):
            k=o['actionType'];n=o.get('source');source=next((x for x in own['hand']+field if x.get('ref')==o.get('sourceRef')), {})
            if k=='AttackAction':return attack_score(o.get('attack',{}),active,own,other)
            if k=='EvolvePokemonAction':return 350 if n=='Gardevoir ex' else 310 if n=='Kirlia' else 150
            if k=='UseAbilityAction' and n=='Gardevoir ex':
                return 500+sequence['attackValue']/10 if sequence and sequence['embraceTarget'] and not sequence['manualSource'] else -200
            if k=='UseAbilityAction' and n in ('Mew ex','Fezandipiti ex'):return 280
            if k=='PlayPokemonAction':
                if n=='Ralts':return 300 if sum(p['field'].get(x,0) for x in LINE)<2 else -130
                if n in ATTACKERS or n in ('Munkidori','Budew','Mew ex','Fezandipiti ex','Flutter Mane'):
                    return 200 if not p['field'].get(n) and len(own['bench'])<4 else -130
            if k=='AttachEnergyAction':
                target=next((c for c in field if c.get('ref')==o.get('targetRef')), {})
                return manual_energy_value(source,target,own,other,sequence)
            if k=='UseItemAction':
                if n=='Rare Candy':return 345
                if n in ('Buddy-Buddy Poffin','Poké Pad','Ultra Ball','Secret Box'):return 240 if not p['field'].get('Gardevoir ex') else 140
                if n=='Night Stretcher':return max((resource(c,p,own,other) for c in own['discard']),default=0)
                if n=='Earthen Vessel':return 190 if not p['hand'].get('Psychic Energy') and p['discardPsychic']<2 else 80
                if n=='Super Rod':return 120 if any(c.get('name') in LINE+ATTACKERS for c in own['discard']) else -100
            if k=='UseSupporterAction':
                if n=='Arven':return 230 if not p['field'].get('Gardevoir ex') else 110
                if n in ('Iono',"Professor's Research", "Lillie's Determination"):
                    return 150+max(0,5-len(own['hand']))*20+(50 if n=="Professor's Research" and p['field'].get('Gardevoir ex') and p['hand'].get('Psychic Energy') and p['discardPsychic']<2 else 0)
            if k=='UseToolAction' and n=='Bravery Charm':return 295
            if k=='UseStadiumAction' and n=='Artazon':return 250 if 'Ralts' in p['needs'] else 75
            if k=='RetreatAction':
                if sequence and sequence['switch'] and not sequence['manualSource'] and not sequence['embraceTarget']:return 500+sequence['attackValue']/10
                gain=max((ready(c,own,other) for c in own['bench']),default=-100)-ready(active,own,other)
                return 180+gain if gain>20 else -150
            return 130 if o['id']==original['choice'].get('optionId') else {'UseAbilityAction':120,'UseItemAction':100,'UseToolAction':95,'PutStadiumAction':80,'PassTurn':-100}.get(k,0)
        best=max(d['options'],key=score);cmd={**cmd,'choice':{'optionId':best['id']}};reason={'action':best['actionType'],'source':best.get('source'),'value':score(best)}
    assert legal(dto,cmd),'INVALID_EXPERT'
    ms=(time.perf_counter()-start)*1000+detail['aiMilliseconds'];assert ms<1000
    return cmd,{'route':VERSION,'fallback':False,'aiMilliseconds':ms,'reason':reason,'plan':p}

"""Exact-list Joltik Box basic expert. Public-only role/energy planning."""
from collections import Counter
from pathlib import Path
import json,time
from apps.api.expert_policies.grimmsnarl import deck_hash,environment_hash
from apps.api.expert_policies.energy import energy_priority
from packages.battle import h2
from packages.battle.h2_v4 import policy as base
from packages.battle.h2_v4.tempo import attack_value,estimate
from packages.simulation.a2 import legal
PROFILE=json.loads((Path(__file__).parent/'joltik-profile.json').read_text())
VERSION='h2-joltik-basic-v1'
ATTACKERS=('Iron Hands ex','Miraidon ex','Iron Leaves ex','Pikachu ex','Bloodmoon Ursaluna ex')

def attack_score(a,c,own,other):
    value=attack_value(a,c,own,other)
    if a.get('name')=='Amp You Very Much' and estimate(a,c,own,other)>=(other['active'] or [{}])[0].get('hp',9999):value+=140
    if a.get('name')=='Explosive Vortex' and value<400:value-=25 # reserve costly finisher when no KO
    return value

def ready(c,own,other):
    if c.get('attackBlocked'):return -100
    return max((attack_score(a,c,own,other) for a in c.get('attacks',[]) if a.get('name')!='Jolting Charge' and a.get('name') not in c.get('limitedAttacks',[]) and base.deficit(a.get('cost',[]),c.get('energy',[]))==0),default=-100)

def charge_value(c,own,other):
    """Recipient ranking robust to grass/lightning-only acceleration, without hidden queue."""
    if c.get('name') not in ATTACKERS or c.get('attackBlocked'):return -150
    if ready(c,own,other)>0:return -80 # fill a second attacker once one is ready
    e=c.get('energy',[]);values=[]
    for a in c.get('attacks',[]):
        cost=a.get('cost',[]);missing=base.deficit(cost,e)
        off=sum(max(0,n-Counter(e)[t]) for t,n in Counter(cost).items() if t not in ('COLORLESS','GRASS','LIGHTNING'))
        values.append(220-missing*28-off*110+min(attack_score(a,c,own,other),500)/8+(35 if c.get('name')=='Iron Hands ex' else 0))
    return max(values,default=-150)

def manual_energy(source,c,own,other):
    score=energy_priority(source,c,own,other)
    if c.get('name')=='Joltik':
        return 290 if not c.get('energy') and not any(ready(x,own,other)>0 for x in own['active']+own['bench']) else -150
    if c.get('name') in ATTACKERS and not c.get('attackBlocked'):
        provides=source.get('provides') or [source.get('cardType')]
        if any(base.deficit(a.get('cost',[]),c.get('energy',[])+provides)<base.deficit(a.get('cost',[]),c.get('energy',[])) for a in c.get('attacks',[])):
            score=max(score,150+charge_value(c,own,other)/3)
    return score

def plan(dto):
    own=dto['observation']['self'];other=dto['observation']['opponent'];field=own['active']+own['bench'];names=Counter(c['name'] for c in field);needs=[]
    if not names['Joltik'] and not any(ready(c,own,other)>0 for c in field):needs.append('Joltik')
    if not names['Miraidon ex']:needs.append('Miraidon ex')
    if not names['Iron Hands ex']:needs.append('Iron Hands ex')
    if not names['Latias ex']:needs.append('Latias ex')
    hand=Counter(c['name'] for c in own['hand'])
    return {'stateVersion':dto['stateVersion'],'goal':'charge-main-then-promote-and-prepare-backup','needs':needs,'field':dict(names),'hand':dict(hand),'readyAttackers':sum(ready(c,own,other)>0 for c in field)}

def resource(c,p,own,other):
    n=c.get('name')
    if c.get('superType')=='ENERGY':
        value=max((manual_energy(c,x,own,other) for x in own['active']+own['bench']),default=0)
        return value if not p['hand'].get(n) else min(35,value)
    if n in p['needs']:return 290-p['needs'].index(n)*20 if not p['hand'].get(n) else 30
    if n=='Crispin' and not p['readyAttackers']:return 220
    if n=='Nest Ball' and p['needs']:return 210
    if n in ('Switch','Rescue Board','Future Booster Energy Capsule') and not p['field'].get('Latias ex') and p['readyAttackers']:return 230
    return base.utility(c,own)

def decide(dto,*,entries,environment):
    if dto['observation']['opponent']['hand'] is not None:raise ValueError('HIDDEN_HAND_EXPOSED')
    cmd,detail=h2.decide(dto)
    if deck_hash(entries)!=PROFILE['deckHash'] or environment!=PROFILE['environmentHash']:return cmd,{**detail,'route':'frozen-h2'}
    start=time.perf_counter();own=dto['observation']['self'];other=dto['observation']['opponent'];field=own['active']+own['bench'];active=(own['active'] or [{}])[0];d=dto['decision'];p=plan(dto);reason='shared'
    if d['kind']=='selection':
        cs=d['candidates'];cost=d.get('zones')==['CardPosition.HAND'] and dto.get('phase')=='playing';ordered=None;count=d['min'] if cost else min(d['max'],len(cs))
        if cs and not d.get('hidden'):
            if d.get('source') in ('Joltik','Crispin') and all(x.get('side')=='self' for x in cs):
                ordered=sorted(cs,key=lambda x:charge_value(x['card'],own,other),reverse=True);reason='concentrate-charge-then-backup'
            elif dto.get('phase')=='active' or (d.get('zones')==['CardPosition.BENCH'] and all(x.get('side')=='self' for x in cs)):
                ordered=sorted(cs,key=lambda x:max(ready(x['card'],own,other),170 if x['card'].get('name')=='Joltik' and not p['readyAttackers'] else -100),reverse=True);reason='promote-ready-attacker-or-charge'
            elif cost:
                keep=set()
                for n in p['needs']:
                    x=next((x for x in cs if x['card'].get('name')==n),None)
                    if x:keep.add(x['ref'])
                ordered=sorted(cs,key=lambda x:resource(x['card'],p,own,other)+(300 if x['ref'] in keep else 0));reason='pay-surplus-preserve-roles'
            elif not any(x.get('side') or x.get('card',{}).get('kind')=='attack' for x in cs) and dto.get('phase') in ('playing','bench','bonus_bench'):
                ordered=sorted(cs,key=lambda x:resource(x['card'],p,own,other),reverse=True);reason='search-role-or-missing-energy'
                if count>1:
                    unique=[];rest=[];seen=set()
                    for x in ordered:
                        n=x['card'].get('name');(rest if n in seen else unique).append(x);seen.add(n)
                    ordered=unique+rest
            if ordered is not None:cmd={**cmd,'choice':{'selectedRefs':[x['ref'] for x in ordered[:count]]}}
    else:
        original=cmd['choice']['optionId']
        def score(o):
            k=o['actionType'];n=o.get('source')
            if k=='AttackAction':
                if o.get('attack',{}).get('name')=='Jolting Charge':return 175 if not p['readyAttackers'] else -50
                return attack_score(o.get('attack',{}),active,own,other)
            if k=='AttachEnergyAction':
                source=next((x for x in own['hand'] if x.get('ref')==o.get('sourceRef')),{});c=next((x for x in field if x.get('ref')==o.get('targetRef')),{});return manual_energy(source,c,own,other)
            if k=='PlayPokemonAction':
                if n in p['needs']:return 280-p['needs'].index(n)*15
                if n in ATTACKERS:return 180 if not p['field'].get(n) and len(own['bench'])<4 else -150
                if n in ('Mew ex','Fezandipiti ex',"Lillie's Clefairy ex"):return 140 if not p['field'].get(n) and len(own['bench'])<4 else -150
            if k=='UseAbilityAction' and n=='Miraidon ex':return 320 if p['needs'] else -100
            if k=='UseAbilityAction' and n in ('Mew ex','Fezandipiti ex'):return 285
            if k=='EvolvePokemonAction' and n=='Galvantula':return -120 if not p['readyAttackers'] else 150
            if k=='UseSupporterAction':
                if n=='Crispin':return 270 if not p['readyAttackers'] else 190
                if n=='Arven':return 235 if p['needs'] else 110
            if k=='UseItemAction':
                if n in ('Nest Ball','Ultra Ball'):return 245 if p['needs'] else 100
                if n=='Earthen Vessel':return 205 if not any(c.get('superType')=='ENERGY' for c in own['hand']) else 90
                if n=='Night Stretcher':return max((resource(c,p,own,other) for c in own['discard']),default=0)
            if k=='RetreatAction' or k=='UseItemAction' and n=='Switch':
                gain=max((ready(c,own,other) for c in own['bench']),default=-100)-ready(active,own,other)
                return 200+gain if gain>20 else -150
            return 130 if o['id']==original else {'UseAbilityAction':120,'UseItemAction':100,'UseToolAction':95,'PutStadiumAction':85,'UseSupporterAction':100,'PassTurn':-100}.get(k,0)
        chosen=max(d['options'],key=score);cmd={**cmd,'choice':{'optionId':chosen['id']}};reason={'action':chosen['actionType'],'source':chosen.get('source'),'value':score(chosen)}
    assert legal(dto,cmd),'INVALID_EXPERT'
    ms=(time.perf_counter()-start)*1000+detail['aiMilliseconds'];assert ms<1000
    return cmd,{'route':VERSION,'fallback':False,'aiMilliseconds':ms,'plan':p,'reason':reason}

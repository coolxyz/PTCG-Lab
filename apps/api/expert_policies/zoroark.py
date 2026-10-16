"""Basic N's Zoroark exact-list expert; only own list and public DTO inputs."""
from collections import Counter
from pathlib import Path
import json,time
from apps.api.expert_policies.grimmsnarl import deck_hash,environment_hash
from apps.api.expert_policies.energy import energy_priority
from packages.battle import h2
from packages.battle.h2_v4 import policy as base
from packages.battle.h2_v4.tempo import attack_value
from packages.battle.h2_v4.visible_math import damage,mechanic
from packages.simulation.a2 import legal
PROFILE=json.loads((Path(__file__).parent/'zoroark-profile.json').read_text())
VERSION='h2-zoroark-basic-v2'
BASIC="N's Zorua";MAIN="N's Zoroark ex";RESH="N's Reshiram";DAR="N's Darmanitan";SIG="N's Sigilyph"

def plan(dto):
    own=dto['observation']['self'];names=Counter(c['name'] for c in own['active']+own['bench']);hand=Counter(c['name'] for c in own['hand']);needs=[]
    if names[BASIC] and names[MAIN]<2:needs.append(MAIN)
    if names[BASIC]+names[MAIN]<2:needs.append(BASIC)
    if not names[RESH]:needs.append(RESH)
    if own['prize_count']<=2 and not names[SIG]:needs.append(SIG)
    if not names[DAR]:needs.append(DAR if names["N's Darumaka"] else "N's Darumaka")
    return {'stateVersion':dto['stateVersion'],'goal':'dual-dark-copy-from-bench','needs':needs,'field':dict(names),'hand':dict(hand),'discardDark':sum(c.get('name')=='Darkness Energy' for c in own['discard'])}

def copied_score(attack,donor,attacker,own,other):
    rule=mechanic(donor,attack);target=(other['active'] or [{}])[0]
    if rule.get('operation')=='win_one_prize':return 10000 if own['prize_count']==1 else -100
    if rule.get('term')=='discard_energy':
        cards=other['discard'] if rule.get('owner')=='opponent' else own['discard']
        n=sum(c.get('superType')=='ENERGY' and (not rule.get('basic') or c.get('energyType','BASIC')=='BASIC') for c in cards)
        amount=damage({**attack,'damage':n*rule['factor']},attacker,target,own,other,{})
    else:amount=damage(attack,attacker,target,own,other,rule)
    value=(400+40*target.get('prize',1) if amount>=target.get('hp',9999)>0 else 65)+min(amount,600)/8
    if rule.get('kind')=='sequence' and any(s.get('kind')=='discard_self_energy' for s in rule.get('steps',[])):
        # Retain the copied attack's energy cost; allow a public bench KO to compensate.
        bench_ko=max((c.get('prize',1) for c in other['bench'] if 0<c.get('hp',9999)<=90),default=0)
        value+=120*bench_ko-65
    return value

def copies(c,own,other):
    return [(copied_score(a,d,c,own,other),a,d) for d in own['bench'] if d.get('name','').startswith("N's ") for a in d.get('attacks',[]) if mechanic(d,a).get('kind')!='copy_attack']

def ready(c,own,other):
    if c.get('attackBlocked'):return -100
    if c.get('name')==MAIN:
        return max((x[0] for x in copies(c,own,other)),default=-100) if base.deficit(['DARK','DARK'],c.get('energy',[]))==0 else -100
    from packages.battle.h2_v4.tempo import ready_value
    return ready_value(c,own,other,base.deficit)

def escape_need(c,own,other):
    if c not in own['active'] or own.get('turnFlags',{}).get('retreated') or c.get('retreatBlocked'):return False
    retreat=len(c.get('retreatCost',[]))
    return len(c.get('energy',[]))<retreat and any(ready(b,own,other)>max(0,ready(c,own,other))+20 for b in own['bench'])

def energy(c,own,other=None):
    if other is not None and escape_need(c,own,other):return 390
    if c.get('name') in (BASIC,MAIN):
        dark=c.get('energy',[]).count('DARK')
        return (280 if dark==1 else 215)+(20 if c in own['active'] else 0)+(15 if c['name']==MAIN else 0) if dark<2 else -100
    return -100

def resource(c,p,own):
    n=c.get('name')
    if n in p['needs']:return 290-p['needs'].index(n)*20 if not p['hand'].get(n) else 30
    if n=='Darkness Energy':return 190 if not p['hand'].get(n) else 25
    if n=="N's PP Up":return 240 if p['discardDark'] and any(x['name'] in (BASIC,MAIN) and x.get('energy',[]).count('DARK')<2 for x in own['bench']) else 40
    if n in ('Poké Pad','Ultra Ball','Buddy-Buddy Poffin','Nest Ball') and p['needs']:return 175
    return base.utility(c,own)

def decide(dto,*,entries,environment):
    if dto['observation']['opponent']['hand'] is not None:raise ValueError('HIDDEN_HAND_EXPOSED')
    cmd,detail=h2.decide(dto)
    if deck_hash(entries)!=PROFILE['deckHash'] or environment!=PROFILE['environmentHash']:return cmd,{**detail,'route':'frozen-h2'}
    start=time.perf_counter();own=dto['observation']['self'];other=dto['observation']['opponent'];field=own['active']+own['bench'];active=(own['active'] or [{}])[0];d=dto['decision'];p=plan(dto);reason='shared'
    if d['kind']=='selection':
        cs=d['candidates'];cost=d.get('zones')==['CardPosition.HAND'] and dto.get('phase')=='playing';ordered=None;count=d['min'] if cost else min(d['max'],len(cs))
        if cs and not d.get('hidden'):
            if all(x.get('card',{}).get('kind')=='attack' for x in cs) and active.get('name')==MAIN:
                def score(x):
                    a=x['card'];donor=next((c for c in own['bench'] if any(z['name']==a['name'] for z in c.get('attacks',[]))),{})
                    return copied_score(a,donor,active,own,other)
                ordered=sorted(cs,key=score,reverse=True);reason='choose-copy-for-current-attacker'
            elif d.get('source')=="N's PP Up" and all(x.get('side')=='self' for x in cs):
                ordered=sorted(cs,key=lambda x:energy(x['card'],own),reverse=True);reason='pp-up-fund-copy-user'
            elif dto.get('phase')=='active' or (d.get('zones')==['CardPosition.BENCH'] and all(x.get('side')=='self' for x in cs)):
                ordered=sorted(cs,key=lambda x:ready(x['card'],own,other)+(80 if x['card'].get('name')==BASIC else 0),reverse=True);reason='promote-copy-user'
            elif cost:
                keep=set()
                for n in p['needs']:
                    x=next((x for x in cs if x['card'].get('name')==n),None)
                    if x:keep.add(x['ref'])
                manual_needed=not own.get('turnFlags',{}).get('energyPlayed',True) and any(energy(c,own,other)>0 for c in field)
                if manual_needed:
                    first=next((x for x in cs if x['card'].get('name')=='Darkness Energy'),None)
                    if first:keep.add(first['ref'])
                ordered=sorted(cs,key=lambda x:(-80 if x['card'].get('name')=='Darkness Energy' and p['hand'].get("N's PP Up") and p['discardDark']<2 else resource(x['card'],p,own))+(300 if x['ref'] in keep else 0));reason='trade-surplus-fuel-pp-up'
            elif not any(x.get('side') or x.get('card',{}).get('kind')=='attack' for x in cs) and dto.get('phase')=='playing':
                ordered=sorted(cs,key=lambda x:resource(x['card'],p,own),reverse=True);reason='search-role'
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
                if o.get('attack',{}).get('name')=='Night Joker':return max((x[0] for x in copies(active,own,other)),default=-100)
                return attack_value(o.get('attack',{}),active,own,other)
            if k=='EvolvePokemonAction':return 350 if n==MAIN else 290 if n==DAR else 150
            if k=='UseAbilityAction' and n in (MAIN,'Mew ex','Fezandipiti ex'):return 300
            if k=='PlayPokemonAction':
                if n==BASIC:return 290 if p['field'].get(BASIC,0)+p['field'].get(MAIN,0)<2 else -150
                if n in (RESH,SIG,"N's Darumaka"):
                    if n==SIG and own['prize_count']>2:return -150
                    return 270 if n==SIG and own['prize_count']==1 else 245 if not p['field'].get(n) and len(own['bench'])<5 else -150
                if n in ('Pecharunt ex','Mew ex','Fezandipiti ex',"N's Joltik"):return 160 if not p['field'].get(n) and len(own['bench'])<4 else -150
            if k=='AttachEnergyAction':
                c=next((x for x in field if x.get('ref')==o.get('targetRef')),{});return energy(c,own,other)
            if k=='UseItemAction':
                if n=="N's PP Up":return 330 if any(energy(c,own)>0 for c in own['bench']) else -150
                if n in ('Poké Pad','Buddy-Buddy Poffin','Nest Ball','Ultra Ball','Secret Box'):return 235 if p['needs'] else 100
                if n=='Earthen Vessel':return 230 if not p['hand'].get('Darkness Energy') else 80
                if n=='Night Stretcher':return max((resource(c,p,own) for c in own['discard']),default=0)
            if k=='UseSupporterAction':return 220 if n=='Arven' and p['needs'] else 100+max(0,5-len(own['hand']))*20
            if k=='RetreatAction' or k=='UseAbilityAction' and n=='Pecharunt ex':
                gain=max((ready(c,own,other) for c in own['bench'] if k=='RetreatAction' or c.get('cardType')=='DARK' and c.get('name')!='Pecharunt ex'),default=-100)-ready(active,own,other)
                return 180+gain if gain>20 else -150
            return 130 if o['id']==original else {'UseAbilityAction':120,'UseItemAction':100,'UseToolAction':95,'PutStadiumAction':85,'PassTurn':-100}.get(k,0)
        chosen=max(d['options'],key=score);cmd={**cmd,'choice':{'optionId':chosen['id']}};reason={'action':chosen['actionType'],'source':chosen.get('source'),'value':score(chosen)}
    assert legal(dto,cmd),'INVALID_EXPERT'
    ms=(time.perf_counter()-start)*1000+detail['aiMilliseconds'];assert ms<1000
    return cmd,{'route':VERSION,'fallback':False,'aiMilliseconds':ms,'plan':p,'reason':reason}

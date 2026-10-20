"""Basic exact-list Dragapult expert. Only current public DTO and own list inputs."""
from collections import Counter
from pathlib import Path
import json,time
from apps.api.expert_policies.grimmsnarl import deck_hash,environment_hash
from apps.api.expert_policies.energy import energy_priority,recovery_energy_value
from packages.battle import h2
from packages.battle.h2_v4 import policy as base
from packages.battle.h2_v4.tempo import attack_value,ready_value
from packages.simulation.a2 import legal
PROFILE=json.loads((Path(__file__).parent/'dragapult-profile.json').read_text())
VERSION='h2-dragapult-basic-v1'
LINE=('Dreepy','Drakloak','Dragapult ex')

def plan(dto):
    own=dto['observation']['self'];field=own['active']+own['bench']
    names=Counter(c['name'] for c in field);hand=Counter(c['name'] for c in own['hand'])
    needs=[]
    if sum(names[n] for n in LINE)<2:needs.append('Dreepy')
    if names['Dreepy'] and not hand['Drakloak']:needs.append('Drakloak')
    if names['Drakloak'] or (names['Dreepy'] and hand['Rare Candy']):needs.insert(0,'Dragapult ex')
    return {'goal':'sustain-phantom-dive','needs':list(dict.fromkeys(needs)),
            'field':dict(names),'hand':dict(hand),'stateVersion':dto['stateVersion']}

def energy(source,target,own,other):
    value=energy_priority(source,target,own,other)
    if target.get('name') in LINE:
        provides=source.get('provides') or [source.get('cardType')]
        have=target.get('energy',[])
        if any(t in ('FIRE','PSYCHIC') and t not in have for t in provides):
            value=max(value,220+(35 if target['name']=='Dragapult ex' else 0)+(20 if have else 0))
    return value

def resource(card,p,own,other):
    n=card.get('name');hand=p['hand'];field=p['field']
    if card.get('superType')=='ENERGY':
        value=recovery_energy_value(card,own,other)
        if not own.get('turnFlags',{}).get('energyPlayed',True) and not hand.get(n):
            value=max(value,max((energy(card,c,own,other) for c in own['active']+own['bench']),default=0))
        return value
    if n in p['needs']:return 280-p['needs'].index(n)*25 if not hand.get(n) else 35
    if n=='Rare Candy' and field.get('Dreepy') and hand.get('Dragapult ex'):return 260
    if n in ('Buddy-Buddy Poffin','Poké Pad','Ultra Ball') and p['needs']:return 170
    if n=='Sparkling Crystal' and any(field.get(x) for x in LINE):return 180
    if n=='Arven' and p['needs']:return 160
    return base.utility(card,own)

def damage_target(c):
    # Six independent visible prompts; zero-HP targets stay on board until allocation ends.
    hp=c.get('hp',999);prize=c.get('prize',1)
    if hp<=0:return -10000
    if hp<=10:return 1000+100*prize
    if hp<=60:return 500+40*prize-hp
    # Prepare a later 200-damage Phantom Dive KO; otherwise concentrate damage.
    return (200 if 200<hp<=260 else 0)+20*prize-hp/10

def decide(dto,*,entries,environment):
    if dto['observation']['opponent']['hand'] is not None:raise ValueError('HIDDEN_HAND_EXPOSED')
    original,detail=h2.decide(dto)
    if deck_hash(entries)!=PROFILE['deckHash'] or environment!=PROFILE['environmentHash']:
        return original,{**detail,'route':'frozen-h2'}
    start=time.perf_counter();p=plan(dto);own=dto['observation']['self'];other=dto['observation']['opponent']
    field=own['active']+own['bench'];active=(own['active'] or [{}])[0];d=dto['decision'];cmd=original;reason='shared'
    if d['kind']=='selection':
        cs=d['candidates'];cost=d.get('zones')==['CardPosition.HAND'] and dto.get('phase')=='playing'
        if cs and not d.get('hidden'):
            if d.get('source')=='Dragapult ex' and all(c.get('side')=='opponent' for c in cs):
                chosen=max(cs,key=lambda c:damage_target(c['card']));cmd={**cmd,'choice':{'selectedRefs':[chosen['ref']]}};reason='allocate-phantom-dive'
            elif cost:
                protected=set()
                for name in p['needs']+['Fire Energy','Psychic Energy','Rare Candy']:
                    match=next((c for c in cs if c['card'].get('name')==name),None)
                    if match:protected.add(match['ref'])
                ordered=sorted(cs,key=lambda c:resource(c['card'],p,own,other)+(300 if c['ref'] in protected else 0))
                cmd={**cmd,'choice':{'selectedRefs':[c['ref'] for c in ordered[:d['min']]]}};reason='pay-surplus'
            elif not any(c.get('side') or c.get('card',{}).get('kind')=='attack' for c in cs) and dto.get('phase')=='playing':
                # Draw/search/recovery only; board and copied attack prompts retain shared logic.
                ordered=sorted(cs,key=lambda c:resource(c['card'],p,own,other),reverse=True)
                if d['max']>1:
                    unique=[];rest=[];seen=set()
                    for c in ordered:
                        n=c['card'].get('name');(rest if n in seen else unique).append(c);seen.add(n)
                    ordered=unique+rest
                cmd={**cmd,'choice':{'selectedRefs':[c['ref'] for c in ordered[:min(d['max'],len(cs))]]}};reason='search-main-plan'
    else:
        def score(o):
            k=o['actionType'];n=o.get('source');source=next((c for c in own['hand']+field if c.get('ref')==o.get('sourceRef')), {})
            if k=='AttackAction':return attack_value(o.get('attack',{}),active,own,other)
            if k=='UseAbilityAction' and n=='Drakloak':return 370 # draw before evolving this source
            if k=='EvolvePokemonAction':return 340 if n=='Dragapult ex' else 310 if n=='Drakloak' else 175
            if k=='AttachEnergyAction':
                target=next((c for c in field if c.get('ref')==o.get('targetRef')),{});return energy(source,target,own,other)
            if k=='PlayPokemonAction':
                if n=='Dreepy':return 290 if sum(p['field'].get(x,0) for x in LINE)<2 else -130
                if n=='Duskull':return 150 if not any(p['field'].get(x) for x in ('Duskull','Dusclops','Dusknoir')) else -130
                if n in ('Mew ex','Fezandipiti ex','Budew','Munkidori','Hawlucha'):return 150 if not p['field'].get(n) and len(own['bench'])<4 else -130
            if k=='UseItemAction':
                if n=='Rare Candy':return 335 if p['hand'].get('Dragapult ex') else 175
                if n in ('Buddy-Buddy Poffin','Poké Pad','Ultra Ball'):return 235 if p['needs'] else 105
                if n in ('Night Stretcher','Earthen Vessel'):return max((resource(c,p,own,other) for c in own['discard']),default=0) if n=='Night Stretcher' else 205 if not any(p['hand'].get(x) for x in ('Fire Energy','Psychic Energy')) else 80
            if k=='UseSupporterAction':
                if n=='Arven':return 230 if p['needs'] else 110
                if n in ('Iono',"Lillie's Determination"):return 100+max(0,5-len(own['hand']))*20
            if k=='UseStadiumAction' and n=='Artazon':return 240 if 'Dreepy' in p['needs'] else 75
            if k=='RetreatAction':
                gain=max((ready_value(c,own,other,base.deficit) for c in own['bench']),default=-100)-ready_value(active,own,other,base.deficit)
                return 180+gain if gain>20 else -150
            return 130 if o['id']==original['choice'].get('optionId') else {'UseAbilityAction':120,'UseToolAction':110,'UseItemAction':100,'PutStadiumAction':80,'PassTurn':-100}.get(k,0)
        best=max(d['options'],key=score);cmd={**cmd,'choice':{'optionId':best['id']}};reason={'action':best['actionType'],'source':best.get('source'),'score':score(best)}
    assert legal(dto,cmd),'INVALID_EXPERT'
    ms=(time.perf_counter()-start)*1000+detail['aiMilliseconds'];assert ms<1000
    return cmd,{'route':VERSION,'fallback':False,'aiMilliseconds':ms,'reason':reason,'plan':p}

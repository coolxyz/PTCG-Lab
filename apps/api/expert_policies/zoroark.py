"""Three scoped components over frozen product v2: pivot, protection, chain."""
import time
from copy import deepcopy
from apps.api.expert_policies import zoroark_basic_v2 as previous
from packages.simulation.a2 import legal
from packages.battle.h2_v4.tempo import attack_value
from apps.api.expert_policies import zoroark_chain_model as m
PROFILE=previous.PROFILE
VERSION='h2-zoroark-copy-chain-v3'
COMPONENTS=frozenset(('pivot','protection','chain'))

def lookup(d,cmd):return next(o for o in d['options'] if o['id']==cmd['choice']['optionId'])
def card_for(own,ref):return next((c for c in m.field(own) if c['ref']==ref),None)
def mature_need(own):return not any(c['name']==m.MAIN for c in m.field(own)) and any(c['name']==m.BASIC and m.mature(own,c) for c in m.field(own))
def win_or_established_attack(dto,cmd):
 d=dto['decision'];own=dto['observation']['self'];other=dto['observation']['opponent']
 if d['kind']!='options':return False
 o=lookup(d,cmd)
 if o['actionType']!='AttackAction' or not own['active']:return False
 a=own['active'][0]
 if a['name']==m.MAIN and previous.ready(a,own,other)>=65:return True
 return attack_value(o.get('attack',{}),a,own,other)>=400

def choose_pivot(dto,cmd):
 own=dto['observation']['self'];other=dto['observation']['opponent'];d=dto['decision']
 if not own['active']:return cmd,None # KO replacement belongs to the next turn.
 if d['kind']=='options':
  baseline=lookup(d,cmd)
  if baseline['actionType'] not in ('PassTurn','RetreatAction','AttackAction'):return cmd,None
  current=previous.ready(own['active'][0],own,other)
  ranked=[(m.projected_ready(own,other,c,pay=True),c) for c in own['bench'] if c['name']==m.MAIN and c.get('energy',[]).count('DARK')>=2]
  retreat=next((o for o in d['options'] if o['actionType']=='RetreatAction'),None)
  if retreat and ranked and max(v for v,c in ranked)>max(0,current)+20:
   return {**cmd,'choice':{'optionId':retreat['id']}},{'component':'pivot','kind':'old-front-becomes-copy-donor','value':max(v for v,c in ranked)}
 elif d['kind']=='selection' and d.get('source') is None and d.get('zones')==['CardPosition.BENCH'] and own.get('turnFlags',{}).get('retreated'):
  ranked=[]
  for c in d.get('candidates',[]):
   target=card_for(own,c.get('boardRef') or c['ref'])
   if c.get('side')=='self' and target:
    ranked.append((m.projected_ready(own,other,target,pay=True),c['ref'] in cmd['choice'].get('selectedRefs',[]),c))
  if ranked:
   value,_,best=max(ranked,key=lambda x:x[:2])
   if value>=65:return {**cmd,'choice':{'selectedRefs':[best['ref']]}},{'component':'pivot','kind':'promote-using-post-retreat-donors','value':value}
 return cmd,None

def protect_cost(dto,cmd):
 d=dto['decision'];own=dto['observation']['self'];cs=d.get('candidates',[])
 if d['kind']!='selection' or d.get('zones')!=['CardPosition.HAND'] or dto.get('phase')!='playing' or d.get('hidden') or not cs:return cmd,None
 keep=set();originally_paid=set(cmd['choice'].get('selectedRefs',[]));mains=[c for c in cs if c.get('card',{}).get('name')==m.MAIN]
 if mains and not any(c['name']==m.MAIN for c in m.field(own)):keep.add(next((c['ref'] for c in mains if c['ref'] not in originally_paid),mains[0]['ref']))
 # Protecting an evolution must not discard every currently usable draw out.
 if mains and not any(c['name']==m.MAIN for c in m.field(own)) and not own.get('turnFlags',{}).get('supporterPlayed'):
  draws=[c for c in cs if c.get('card',{}).get('name') in ('Iono',"Lillie's Determination")]
  if draws:keep.add(next((c['ref'] for c in draws if c['ref'] not in originally_paid),draws[0]['ref']))
 energy=[c for c in cs if c.get('card',{}).get('name')=='Darkness Energy']
 if energy and not own.get('turnFlags',{}).get('energyPlayed') and (any(c['name'] in (m.MAIN,m.BASIC) and c.get('energy',[]).count('DARK')<2 for c in m.field(own)) or mains):keep.add(next((c['ref'] for c in energy if c['ref'] not in originally_paid),energy[0]['ref']))
 ps=m.plans(dto,inspection=False)
 if ps and ps[0].castle:
  castle=next((c for c in cs if c.get('card',{}).get('name')=="N's Castle"),None)
  if castle:keep.add(castle['ref'])
 chosen=cmd['choice'].get('selectedRefs',[]);count=len(chosen)
 if not keep.intersection(chosen):return cmd,None
 p=previous.plan(dto)
 # Discard surplus Dark while retaining the real manual attachment. This also
 # leaves fuel for the registered PP Up; no unseen copy is assumed available.
 def key(c):
  surplus=c.get('card',{}).get('name')=='Darkness Energy' and c['ref'] not in keep and bool(mains)
  return (c['ref'] in keep,-40 if surplus else previous.resource(c.get('card',{}),p,own),c['ref'] not in chosen)
 ordered=sorted(cs,key=key);refs=[c['ref'] for c in ordered[:count]]
 if len(set(refs)&keep)>=len(set(chosen)&keep):return cmd,None
 return {**cmd,'choice':{'selectedRefs':refs}},{'component':'protection','kind':'retain-unique-main-and-required-resource','keptCount':len(keep)}

def choose_chain(dto,cmd):
 own=dto['observation']['self'];other=dto['observation']['opponent'];d=dto['decision'];flags=own.get('turnFlags',{});plans=m.plans(dto)
 if d['kind']=='selection':
  cs=d.get('candidates',[])
  if not cs or d.get('hidden') or d.get('zones')==['CardPosition.HAND']:return cmd,None
  # A real Arven candidate plus a real discarded main is a known retrieval,
  # not a promise that a particular unseen deck card must exist.
  if d.get('source')=='Arven' and mature_need(own) and any(c['name']==m.MAIN for c in own['discard']):
   c=next((c for c in cs if c.get('card',{}).get('name')=='Night Stretcher'),None)
   safe_bench=any(b['name']==m.BASIC and m.mature(own,b) and 'DARK' in b.get('energy',[]) for b in own['bench'])
   executable=bool(c and m.plans(dto,extra=c.get('card',{}),inspection=False))
   if c and (safe_bench or executable):return {**cmd,'choice':{'selectedRefs':[c['ref']]}},{'component':'chain','kind':'arven-to-visible-main-recovery'}
  if d.get('source')=="N's PP Up" and all(c.get('side')=='self' for c in cs):
   # The item and selected fuel have already left hand/discard. Evaluate the
   # real pending allocation once, without requiring a second phantom PP Up.
   ranked=[]
   for c in cs:
    ref=c.get('boardRef') or c['ref'];future=deepcopy(dto);card=card_for(future['observation']['self'],ref)
    if not card:continue
    card['energy']=card.get('energy',[])+['DARK']
    feasible=m.plans(future,inspection=False)
    if feasible:ranked.append((feasible[0].value,card['name']==m.MAIN,c['ref'] in cmd['choice'].get('selectedRefs',[]),c))
   if ranked:
    best=max(ranked,key=lambda x:x[:3]);return {**cmd,'choice':{'selectedRefs':[best[3]['ref']]}},{'component':'chain','kind':'allocate-committed-pp-up','pendingEnergy':1}
  return cmd,None
 if not plans:return cmd,None
 plan=plans[0];options=d['options'];target=card_for(own,plan.target)
 def option(k,source=None,ref=None):return next((o for o in options if o['actionType']==k and (source is None or o.get('source')==source) and (ref is None or o.get('targetRef')==ref)),None)
 chosen=None;kind=None
 if plan.entry=='inspect-arven':chosen=option('UseSupporterAction','Arven');kind='inspect-main-recovery-before-spending-manual'
 elif plan.entry=='recover':chosen=option('UseItemAction','Night Stretcher');kind='recover-before-spending-manual'
 elif plan.entry=='hand':chosen=option('EvolvePokemonAction',m.MAIN,plan.target);kind='evolve-reserved-copy-user'
 elif plan.castle:chosen=option('PutStadiumAction',"N's Castle");kind='play-mobility-before-trade-cost'
 # After the known route is secured, permit actual draw abilities to resolve,
 # then re-plan on their real result rather than hypothesizing the drawn cards.
 elif lookup(d,cmd)['actionType']=='UseAbilityAction' and lookup(d,cmd).get('source') in (m.MAIN,'Mew ex','Fezandipiti ex'):return cmd,None
 elif plan.pp_up:chosen=option('UseItemAction',"N's PP Up");kind='fund-bench-before-promotion'
 elif plan.manual:chosen=option('AttachEnergyAction','Darkness Energy',plan.manual);kind='manual-reserved-by-copy-chain'
 # Retreat itself and its target use the independent pivot component.
 if chosen:return {**cmd,'choice':{'optionId':chosen['id']}},{'component':'chain','kind':kind,'plan':plan.public()}
 return cmd,None

def decide_with(dto,*,entries,environment,enabled=COMPONENTS):
 start=time.perf_counter();cmd,detail=previous.decide(dto,entries=entries,environment=environment)
 if detail.get('route')!=previous.VERSION:return cmd,detail
 notes=[]
 if not win_or_established_attack(dto,cmd):
  if 'protection' in enabled:
   cmd,note=protect_cost(dto,cmd)
   if note:notes.append(note)
  if 'chain' in enabled:
   cmd,note=choose_chain(dto,cmd)
   if note:notes.append(note)
  if 'pivot' in enabled and not any(n['component']=='chain' for n in notes):
   cmd,note=choose_pivot(dto,cmd)
   if note:notes.append(note)
 assert legal(dto,cmd)
 return cmd,{**detail,'route':VERSION,'reason':'copy-chain' if notes else 'unchanged-v2','components':notes,'aiMilliseconds':(time.perf_counter()-start)*1000}

def decide(dto,*,entries,environment):return decide_with(dto,entries=entries,environment=environment)
class Ablation:
 PROFILE=PROFILE
 previous=previous
 def __init__(self,component):self.enabled=COMPONENTS-{component};self.VERSION=VERSION+'-without-'+component
 def decide(self,dto,*,entries,environment):return decide_with(dto,entries=entries,environment=environment,enabled=self.enabled)

BASIC=previous.BASIC
MAIN=previous.MAIN
RESH=previous.RESH
DAR=previous.DAR
SIG=previous.SIG
deck_hash=previous.deck_hash
environment_hash=previous.environment_hash
plan=previous.plan
copies=previous.copies
copied_score=previous.copied_score
ready=previous.ready
energy=previous.energy
escape_need=previous.escape_need
resource=previous.resource
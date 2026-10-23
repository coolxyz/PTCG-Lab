"""Public, finite copy-attack plans with shared manual/bench-energy ledger.
No engine objects, private zones, hypothetical draw results or saved games.
"""
from copy import deepcopy
from dataclasses import dataclass,asdict
from apps.api.expert_policies import zoroark_basic_v2 as v2
from packages.battle.h2_v4.visible_math import counters
MAIN,BASIC=v2.MAIN,v2.BASIC

def field(own):return own['active']+own['bench']
def count(own,name):return sum(c['name']==name for c in own['hand'])
def dark_discard(own):return sum(c['name']=='Darkness Energy' for c in own['discard'])
def mature(own,c):return not own.get('turnFlags',{}).get('firstPersonalTurn') and not c.get('firstTurnPlayed',True)
def after_promotion(own,target,*,pay=True,poison=False):
 """The old front becomes a donor; remove the new front from the donor zone.
 A committed retreat prompt still exposes the old front before payment.
 """
 side=deepcopy(own);new=next((c for c in side['bench'] if c['ref']==target['ref']),None)
 if new is None:return None
 old=(side['active'] or [None])[0]
 if old and pay:
  cost=len(old.get('retreatCost',[]))
  if len(old.get('energy',[]))<cost:return None
  for _ in range(cost):
   energy=old['energy'].pop(0);side['discard'].append({'name':'Darkness Energy' if energy=='DARK' else energy,'superType':'ENERGY','energyType':'BASIC'})
 side['bench']=[c for c in side['bench'] if c['ref']!=new['ref']]+([old] if old else [])
 side['active']=[new]
 if poison:new['specialCondition']='POISONED'
 return side

def projected_ready(own,other,target,*,pay=True,poison=False):
 side=after_promotion(own,target,pay=pay,poison=poison)
 return v2.ready(side['active'][0],side,other)-(10 if poison else 0) if side else -100

@dataclass(frozen=True)
class CopyPlan:
 target:str
 entry:str
 manual:str|None
 pp_up:int
 castle:bool
 mobility:str
 value:float
 inspection:bool=False
 def public(self):return asdict(self)

def plans(dto,*,extra=None,inspection=True):
 own=deepcopy(dto['observation']['self']);other=dto['observation']['opponent'];flags=own.get('turnFlags',{})
 if extra:own['hand'].append(deepcopy(extra))
 if not own['active'] or dto.get('phase')!='playing' or own['active'][0].get('specialCondition') in ('ASLEEP','PARALYZED','CONFUSED'):return []
 # The first planner owns the player's current attack window only.
 if flags.get('attacksBlocked') or flags.get('firstPersonalTurn') and (dto.get('startingPlayer') or '').lower()==own['id']:return []
 old=own['active'][0];current=v2.ready(old,own,other)
 if old['name']==MAIN and current>=65:return []
 results=[]
 template=next((c for c in own['hand']+own['discard'] if c['name']==MAIN),None)
 for c in field(own):
  if c['name']==MAIN:entry='field'
  elif c['name']==BASIC and mature(own,c) and template:
   if count(own,MAIN):entry='hand'
   elif count(own,'Night Stretcher') and not flags.get('itemsBlocked'):entry='recover'
   elif inspection and count(own,'Arven') and not flags.get('supporterPlayed'):entry='inspect-arven'
   else:continue
  else:continue
  need=max(0,2-c.get('energy',[]).count('DARK'));bench=c in own['bench']
  pp_max=min(need,count(own,"N's PP Up"),dark_discard(own)) if bench and not flags.get('itemsBlocked') else 0
  manual_available=not flags.get('energyPlayed') and count(own,'Darkness Energy')>0
  for pp in range(pp_max+1):
   manual_target=need-pp
   if manual_target not in (0,1) or manual_target and not manual_available:continue
   side=deepcopy(own);target=next(b for b in field(side) if b['ref']==c['ref'])
   if entry!='field':
    evolved={**deepcopy(template),'ref':c['ref'],'hp':max(1,280-counters(c)*10),'energy':list(c.get('energy',[])),'tool':list(c.get('tool',[])),'firstTurnPlayed':True,'abilityUsed':False}
    zone=side['bench'] if bench else side['active'];zone[zone.index(target)]=evolved;target=evolved
   # Only actual hand energy and actual discarded PP Up fuel fund this proof.
   target['energy']=target.get('energy',[])+['DARK']*need
   mobility='none';castle=False;manual=c['ref'] if manual_target else None
   if bench:
    if flags.get('retreated') or old.get('retreatBlocked'):continue
    cost=len(old.get('retreatCost',[]));have=len(old.get('energy',[]))
    if cost>have and count(own,"N's Castle") and old['name'].startswith("N's "):
     castle=True;cost=0;side['active'][0]['retreatCost']=[]
    if cost>have:
     if manual_target or not manual_available or cost>have+1:continue
     manual=old['ref'];side['active'][0]['energy']=side['active'][0].get('energy',[])+['DARK']
    promoted=after_promotion(side,target,pay=True)
    if not promoted:continue
    side=promoted;mobility='retreat';target=side['active'][0]
   value=v2.ready(target,side,other)
   if value<65 or value<current+20:continue
   results.append(CopyPlan(c['ref'],entry,manual,pp,castle,mobility,value,entry=='inspect-arven'))
 return sorted(results,key=lambda p:(p.value,-int(p.inspection),-p.pp_up,-int(p.castle),p.entry=='field'),reverse=True)

"""Bind the final reviewed GHIJ faces to explicit, tested rule definitions."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from packages.sync.common import read,write,digest
from scripts.sync.review_support import trainer_text

def field(op,**kw):return dict(kind='field_operation',operation=op,**kw)
def expr(term,factor,**kw):return dict(kind='damage_expression',term=term,factor=factor,mode='add',**kw)
def passive(kind,**kw):return dict(kind=kind,trigger='passive',**kw)
def entry(trigger,op,**kw):return dict(kind='on_entry',trigger=trigger,effect=dict(kind='expanded_entry',op=op,**kw))

ATTACKS={
 '11375':{1:field('shuffle_pair')},
 '11378':{0:{'kind':'sequence','steps':[expr('empty_hand',120),field('conditional_effects',condition={'term':'empty_hand'},effects=[{'kind':'special_status','coin':False,'target':'opponent','status':'POISONED','statuses':['POISONED','PARALYZED']}])]}},
 '11403':{0:expr('played_supporter',90)},
 '11428':{1:field('bench_attack',benchAttack=True)},
 '14637':{1:field('bench_attack',benchAttack=True)},
 '11496':{0:field('distinct_types_search',count=3)},
 '11500':{0:field('choose_weakness')},
 '11505':{1:field('devolve',activeOnly=True,destination='hand')},
 '12667':{0:{'kind':'coin_branch','flips':1,'heads':[field('choose_status')]}},
 '15984':{0:field('supporter_bottom')},
 '17297':{1:field('damaged_spread',benchOnly=True,amount=40)},
 '18025':{0:field('stage_energy_return')},
 '20827':{0:field('bottom_draw',count=3)},
 '21099':{0:{'kind':'damage_shield','amount':0,'maximum':60}},
 '21100':{0:{'kind':'coin_branch','flips':4,'perHead':60,'minimumHeads':2,'heads':[{'kind':'special_status','coin':False,'status':'PARALYZED','target':'opponent'}]}},
 '21116':{1:expr('self_typed_energy',120,type='GRASS',minimum=2)},
 '21122':{0:field('match_hand_draw')},
 '21124':{0:field('opponent_shuffle_hand',count=3)},
 '21248':{0:field('distinct_counters',count=2,amount=30)},
 '21876':{0:field('damaged_spread',amount=50)},
}
ABILITIES={
 '11384':passive('evolution_permission',secondFirstTurn=True),
 '11390':passive('no_trainer_recycle'),
 '11403':passive('continuous',scope='self',specialEnergy=True,hp=100),
 '11493':{**entry('evolve','mill_self',count=5),'mandatory':True},
 '11495':{'kind':'activated_effect','trigger':'activated','activeOnly':True,'firstTurnOnly':True,'effect':{'kind':'expanded_entry','op':'transform_start'}},
 '11502':passive('continuous',scope='opponent',holderZone='active',affectedZone='active',retreatBlocked=True),
 '11504':passive('continuous',scope='opponent',affectedZone='active',weaknessMultiplier=4),
 '18040':entry('bench','heal_cure_one',amount=30),
 '20044':entry('evolve','deploy_opponent'),
 '21137':entry('bench','attach_tool'),
 '21140':entry('evolve','coin_shuffle_hand',flips=2),
}
TRAINERS={
 '10888':('Paradise Resort',{'kind':'stadium_modifier','name':'Psyduck','retreatLess':1}),
 '14644':('Paradise Resort',{'kind':'stadium_modifier','name':'Psyduck','retreatLess':1}),
 '13066':('Antique Helix Fossil',{'kind':'fossil'}),
 '13067':('Antique Dome Fossil',{'kind':'fossil'}),
 '13068':('Antique Old Amber',{'kind':'fossil'}),
 '18754':('Antique Root Fossil',{'kind':'fossil'}),
 '18995':('Antique Root Fossil',{'kind':'fossil'}),
 '18755':('Antique Cover Fossil',{'kind':'fossil'}),
 '18996':('Antique Cover Fossil',{'kind':'fossil'}),
 '18775':('Grand Tree',{'kind':'stadium_modifier','use':'great_tree'}),
}
for cid,name,op in [('14355','Delivery Drone','double_coin_search'),('14362',"Daisy’s Help",'draw_inspect_prizes'),('14363','Parasol Lady','parasol_redraw'),('18060',"Black Belt’s Training",'ex_team_bonus'),('18747','Supporter Bell','supporter_bell'),('18749','Deduction Kit','order_or_bottom'),('18750','Scramble Switch','switch_transfer'),('18752','Relaxing Tail','energy_hand'),('19519',"Larry’s Skill",'discard_search_three'),('20773','Pokemon Center Lady','heal_cure')]:
    TRAINERS[cid]=(name,{'kind':'trainer_operation','op':op,**({'secondFirstTurn':True} if cid in ('18747','18752') else {})})
FOSSILS={
 '13066':passive('hand_lock',activeOnly=True,cards='stadium'),
 '13067':passive('continuous',scope='self',armor=30),
 '13068':passive('ability_protection'),
 '18754':passive('continuous',scope='opponent',holderZone='active',stage='basic',attackMore=1),
 '18755':passive('protection',effects=True),
}
FOSSILS['18995']=FOSSILS['18754'];FOSSILS['18996']=FOSSILS['18755']

def main():
    rows=read('artifacts/sync/reuse-support-summary.json')['remaining']
    clauses=read('data/sync/reviewed-clauses.json');plain=read('data/cardpool/plain-pokemon.json')
    for row in rows:
        cid=str(row['sourceCardId']);f=row['face'];rh=row['ruleHash']
        for kind,items in [('attack',ATTACKS.get(cid,{})),('ability',{0:ABILITIES[cid]} if cid in ABILITIES else {})]:
            for index,definition in items.items():
                txt=f['attacks' if kind=='attack' else 'abilities'][index]['text']
                record={'kind':kind,'ruleHash':rh,'text':txt,'sourceText':txt,'definition':definition,'definitionHash':digest(definition),'effectKey':'final-ghij-reviewed','review':'逐句绑定卡面条件、选择范围、执行者、区域和持续时间；tests/cardpool/test_final_mechanisms.py 验证。'}
                clauses['clauses']=[r for r in clauses['clauses'] if not(r['kind']==kind and r['ruleHash']==rh and r['text']==txt)]+[record]
        if cid in TRAINERS:
            en,mechanic=TRAINERS[cid]
            spec={'effectKey':'P4T-FINAL'+rh[:16].upper(),'name':en,'cardPage':f['name'],'printings':[], 'trainerType':{'竞技场':'stadium','物品':'item','支援者':'supporter'}[f['trainerType']],'text':trainer_text(f),'mechanic':mechanic,'aceSpec':f['specialCard']=='ACE SPEC','sourceAdapter':'chs-final-mechanisms-1','sourceRuleHash':rh}
            if cid in FOSSILS:
                ab=f['abilities'] or [{'name':'原始之根' if 'Root' in en else '背盖守护','text':f['ruleText'].split('|')[1]}]
                spec['abilities']=[{**ab[0],**FOSSILS[cid]}]
                spec['sourceFace']=f
            plain['trainers']=[s for s in plain['trainers'] if s['effectKey']!=spec['effectKey']]+[spec]
        if cid=='20172':
            spec={'effectKey':'P4S-FINAL'+rh[:16].upper(),'name':'Spike Energy','cardPage':f['name'],'energyType':'special','text':f['ruleText'],'mechanic':{'kind':'special_energy','provides':'COLORLESS','retaliation':20},'printings':[],'aceSpec':False,'sourceRuleHash':rh}
            plain['specialEnergies']=[s for s in plain['specialEnergies'] if s['effectKey']!=spec['effectKey']]+[spec]
    write('data/sync/reviewed-clauses.json',clauses);write('data/cardpool/plain-pokemon.json',plain)
if __name__=='__main__':main()

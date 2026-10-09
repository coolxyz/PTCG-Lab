"""Materialize reviewed exact-face reuse for release-bound engine acceptance."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from packages.sync.common import read,write,digest
from packages.sync.normalize import normalize
from scripts.sync.review_support import pokemon_mismatches,trainer_text,norm
from collections import defaultdict
from scripts.sync.review_support import name
import copy


def main():
    sha='ea69b4e3916a717ffe0c0116984c99d3e4a3cb8c'
    n=normalize(read(f'.catalog/sync/snapshots/{sha}/upstream.json'),corrections=read('data/sync/source-corrections.json'))
    plain=read('data/cardpool/plain-pokemon.json')
    specs={s['effectKey']:s for k in ('cards','trainers','specialEnergies') for s in plain[k]}
    effects=read('data/simulation/effects.json');known={e['effectKey']:e for e in effects['effects']}
    proofs={}
    catalog=read('data/catalog/catalog.json')
    # A new printing with the exact complete source rule fingerprint of an
    # already accepted native card adds an alias, not a new mechanism.
    native=defaultdict(dict)
    for card in catalog['cards']:
        key=card.get('engineId','')
        if card.get('effectStatus')!='verified' or not card.get('sourceVerified') or key.startswith(('P4P-','P4T-','P4S-')) or key not in known:continue
        for cid in card.get('upstreamFaces',{}):
            up=n['cards'].get(cid)
            if up and not up['issues']:
                native[up['ruleHash']][key]=card['printingId']
    for rule_hash,anchors in native.items():
        if len(anchors)!=1:continue
        key,pid=next(iter(anchors.items()))
        up=next(c for c in n['cards'].values() if c['ruleHash']==rule_hash)
        proofs[rule_hash]={'effectKey':key,'face':up['face'],'cardId':up['id'],'sourceHash':up['sourceHash'],'method':'native-identical-source-face','anchorPrintingId':pid,'commit':sha}
    for file in ('full-face-review','trainer-face-review'):
        for row in read(f'artifacts/sync/{file}.json'):
            keys={x['effectKey'] for x in row['checks'] if not x['errors']}
            if len(keys)>1 and file=='trainer-face-review':
                semantics={digest({k:specs[key].get(k) for k in ('name','trainerType','mechanic','aceSpec','trait')}) for key in keys}
                if len(semantics)==1:keys={sorted(keys)[0]}
            if len(keys)!=1:continue
            key=next(iter(keys));up=n['cards'][row['cardId']];spec=specs[key]
            assert row['ruleHash']==up['ruleHash']
            if file=='full-face-review':assert not pokemon_mismatches(up['face'],spec)
            else:assert trainer_text(up['face'])==norm(spec['text'])
            proofs[up['ruleHash']]={'effectKey':key,'face':up['face'],'cardId':up['id'],'sourceHash':up['sourceHash'],'specHash':digest(spec),'method':'complete-face-comparison','commit':sha}
            if key not in known:
                known[key]={'effectKey':key,'name':spec['name'],'printings':[],'status':'experimental','registered':True,'mechanisms':['reviewed-compiled-effect'],'implementation':'packages.rules.plain','rulesRevision':'2026-10-07','scope':'source-face-review'}
    energy_keys={'GRASS':'P4E-001','FIRE':'SVE-002','WATER':'P4E-003','LIGHTNING':'P4E-004','PSYCHIC':'SVE-005','FIGHTING':'P4E-006','DARK':'P4E-007','METAL':'SVE-008'}
    for up in n['cards'].values():
        f=up['face']
        if f['category']=='能量' and f['energyType']=='基本能量' and f['type'] in energy_keys and not any(f[k] for k in ('ruleText','attacks','abilities','skills','specialCard')) and not up['issues']:
            proofs[up['ruleHash']]={'effectKey':energy_keys[f['type']],'face':f,'cardId':up['id'],'sourceHash':up['sourceHash'],'method':'ordinary-basic-energy','commit':sha}
    for review in read('data/sync/reviewed-wording.json')['cards']:
        base=n['cards'][review['cardId']];key=review['effectKey']
        assert base['sourceHash']==review['sourceHash'] and digest(specs[key])==review['specHash']
        if review.get('engineName'):
            source_spec=specs[key]
            key='P4T-CHS'+digest(review)[:16].upper()
            tags=(base['face']['specialCard'] or '').split('|')
            cloned={**copy.deepcopy(source_spec),'effectKey':key,'name':review['engineName'],'printings':[],'trait':next(({'古代':'ANCIENT','未来':'FUTURE'}[t] for t in tags if t in ('古代','未来')),None),'aceSpec':'ACE SPEC' in tags,'sourceAdapter':'chs-reviewed-wording-1'}
            cloned['cnName']=base['face']['name']
            if key not in specs:
                specs[key]=cloned;plain['trainers'].append(cloned)
            else:
                specs[key]['cnName']=base['face']['name']
            known.setdefault(key,{'effectKey':key,'name':cloned['name'],'printings':[],'status':'experimental','registered':True,'mechanisms':[cloned['mechanic']['kind']],'implementation':'packages.rules.trainers','rulesRevision':'2026-10-07','scope':'reviewed-source-wording'})
        for up in n['cards'].values():
            f=up['face'];b=base['face']
            if (f['name'],f['category'],f['trainerType'],f['energyType'],f['specialCard'],trainer_text(f))!=(b['name'],b['category'],b['trainerType'],b['energyType'],b['specialCard'],trainer_text(b)):continue
            if up['issues']:continue
            proofs[up['ruleHash']]={'effectKey':key,'face':f,'cardId':up['id'],'sourceHash':up['sourceHash'],'method':'reviewed-wording-equivalence','review':review,'commit':sha}
    # Special Energy implementations are shared only for complete text and
    # printed-name equality. No generic energy or trainer fallback is allowed.
    for up in n['cards'].values():
        f=up['face']
        if f['category']!='能量' or f['energyType']!='特殊能量' or up['issues']:continue
        for spec in plain['specialEnergies']:
            if name(f['name'])==name(spec.get('cardPage','').split('（')[0]) and norm(f['ruleText'])==norm(spec['text']) and bool(f['specialCard']=='ACE SPEC')==spec.get('aceSpec',False):
                proofs[up['ruleHash']]={'effectKey':spec['effectKey'],'face':f,'cardId':up['id'],'sourceHash':up['sourceHash'],'method':'complete-special-energy-face','commit':sha}
    compiled=read('artifacts/sync/compiled-review.json')
    compiled_specs={s['effectKey']:s for s in compiled['specs']}
    for mapping in compiled['mappings']:
        up=n['cards'][mapping['cardId']]
        assert up['ruleHash']==mapping['ruleHash']
        key=mapping['effectKey'];spec=compiled_specs[key]
        proofs[up['ruleHash']]={'effectKey':key,'face':up['face'],'cardId':up['id'],'sourceHash':up['sourceHash'],'method':'reviewed-clause-composition','clauseEvidence':spec['sourceEvidence'],'commit':sha}
        if key not in specs:
            plain['cards'].append(spec);specs[key]=spec
        elif specs[key].get('sourceAdapter')=='chs-reviewed-clauses-1':
            # Reviewed definitions can change while the source face is unchanged.
            # The enclosing engine/effect release pins this implementation update.
            spec['printings']=specs[key]['printings']
            plain['cards'][plain['cards'].index(specs[key])]=spec
            specs[key]=spec
        known.setdefault(key,{'effectKey':key,'name':spec['name'],'printings':[],'status':'experimental','registered':True,'mechanisms':sorted({a.get('mechanic',{}).get('kind','attack_damage') for a in spec['attacks']}),'implementation':'packages.rules.plain.PlainPokemon','rulesRevision':'2026-10-07','scope':'reviewed-source-clauses'})
    cards={}
    for cid,up in n['cards'].items():
        proof=proofs.get(up['ruleHash'])
        if not proof:continue
        key=proof['effectKey'];set_,number=key.split('-',1)
        if key not in known:
            spec=specs[key]
            known[key]={'effectKey':key,'name':spec['name'],'printings':[],'status':'experimental','registered':True,'mechanisms':[spec.get('mechanic',{}).get('kind','reviewed-compiled-effect')],'implementation':'packages.rules.plain','rulesRevision':'2026-10-07','scope':'reviewed-source-wording'}
        cards[cid]={'ruleHash':up['ruleHash'],'effectKey':key,'engineLine':f"{known[key]['name']} {set_} {number}",'testFiles':['tests/sync/test_reviewed_support.py']}
    write('data/sync/reviewed-equivalence.json',{'schema':1,'proofs':proofs})
    write('data/sync/implementations.json',{'schema':1,'cards':cards})
    effects['effects']=list(known.values());write('data/simulation/effects.json',effects)
    write('data/cardpool/plain-pokemon.json',plain)
    print('reviewed rules',len(proofs),'upstream variants',len(cards))


if __name__=='__main__':main()

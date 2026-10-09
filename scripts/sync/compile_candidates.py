"""Compile only complete source faces whose every clause has reviewed semantics.

The output is a review artifact, not a catalogue or an admission flag. New rule
clauses remain explicit gaps. Name/evolution translations use existing reviewed
card headers, never the biological evolution tree.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sync.common import read, write, digest
from packages.sync.normalize import normalize
from packages.sync.attack_grammar import compile_clause
from scripts.sync.review_support import norm, name, trainer_text
from collections import defaultdict, Counter
import copy
import re


def attack_base(attack, mechanic):
    damage=attack['damage']
    if not re.fullmatch(r'\d*[+×x-]?',damage):return None
    value=int(re.match(r'\d*',damage)[0] or '0')
    if damage.endswith(('×','x')):
        if not mechanic:return None
        if mechanic['kind'] not in ('coin_count','coin_until_tails'):value=0
    return value


class Compiler:
    def __init__(self, plain, catalog):
        self.attacks=defaultdict(dict)
        self.abilities=defaultdict(dict)
        self.names=defaultdict(dict)
        self.reviewed=read('data/sync/reviewed-clauses.json')['clauses']
        # Reuse a reviewed clause across complete faces only when its entire
        # normalized wording is identical and the definition is unambiguous.
        for r in self.reviewed:
            assert digest(r['definition'])==r['definitionHash']
            if r['kind']=='attack':
                self.attacks[norm(r['text'])][r['definitionHash']]=(copy.deepcopy(r['definition']),r['effectKey'],'')
            elif r['kind']=='ability':
                self.abilities[norm(r['text'])][r['definitionHash']]=(copy.deepcopy(r['definition']),r['effectKey'])
        bypid={c['printingId']:c for c in catalog['cards']}
        for s in plain['cards']:
            if s.get('sourceAdapter')=='chs-reviewed-clauses-1':
                continue
            names={name(bypid[pid]['cnName']) for pid in s['printings'] if pid in bypid}
            names.add(name(s.get('cardPage',s['name']).split('（')[0]))
            lineage={'name':s['name'],'stage':s['stage'],'evolvesFrom':s['evolvesFrom']}
            for cn in names:
                if not s.get('sourceAdapter'):
                    self.names[(cn,s['stage'])][digest(lineage)]=lineage
            for a in s['attacks']:
                if a.get('text') and a.get('mechanic'):
                    mechanic=a['mechanic']
                    self.attacks[norm(a['text'])][digest(mechanic)]=(copy.deepcopy(mechanic),s['effectKey'],a['name'])
            for a in s.get('abilities',[]):
                mechanic={k:v for k,v in a.items() if k not in ('text','name')}
                self.abilities[norm(a.get('text',''))][digest(mechanic)]=(copy.deepcopy(a),s['effectKey'])
        # Translation only. BASIC is supplied by the printed source stage;
        # no evolutionary relationship is inferred from this species table.
        for row in read('data/cardpool/species-names.json')['species']:
            for suffix in ('','ex'):
                key=(name(row['chinese']+suffix),'BASIC')
                if not self.names[key]:
                    lineage={'name':row['english']+(' ex' if suffix else ''),'stage':'BASIC','evolvesFrom':[]}
                    self.names[key][digest(lineage)]=lineage
        for row in read('data/sync/evolution-evidence.json')['cards']:
            key=(name(row['name']),row['stage'])
            lineage={'name':row['englishName'],'stage':row['stage'],'evolvesFrom':row['evolvesFrom'],'evolutionEvidence':row}
            self.names[key]={digest(lineage):lineage}

    def pokemon(self, up):
        f=up['face'];errors=[];evidence=[]
        if up['issues']:return None,up['issues']
        if f['category']!='宝可梦':return None,['not-pokemon']
        if not isinstance(f['hp'],int) or f['hp']<=0 or not isinstance(f['retreat'],int) or f['retreat']<0 or not f['type']:
            return None,['incomplete-printed-stats']
        if f['pokemonType'] not in (None,'宝可梦ex'):errors.append('pokemon-type')
        if f['skills']:errors.append('skills')
        if f['specialCard'] not in (None,'太晶','古代','未来'):errors.append('special-card')
        if f['weakness'] and f['weaknessFormula']!='×2':errors.append('weakness')
        if f['resistance'] and f['resistanceFormula'] not in ('-30','-20'):errors.append('resistance')
        rule='当宝可梦ex【昏厥】时，对手将拿取2张奖赏卡。' if f['pokemonType']=='宝可梦ex' else ''
        if norm(f['ruleText'])!=norm(rule):errors.append('rule-text')
        lineage=self.names.get((name(f['name']),f['stage']),{})
        if len(lineage)!=1:errors.append('lineage')
        attacks=[]
        for a in f['attacks']:
            effect=None;engine_name=a['name']
            if a['text']:
                matches=self.attacks.get(norm(a['text']),{})
                reviewed=[r for r in self.reviewed if r['kind']=='attack' and r['ruleHash']==up['ruleHash'] and r['text']==a['text']]
                if len(reviewed)==1:
                    r=reviewed[0]
                    assert digest(r['definition'])==r['definitionHash']
                    matches={r['definitionHash']:(r['definition'],r['effectKey'],a['name'])}
                if len(matches)!=1:
                    parsed=compile_clause(norm(a['text']))
                    if parsed:
                        matches={digest(parsed):(parsed,'chs-closed-clauses-1',a['name'])}
                    else:
                        errors.append('attack:'+a['text']);continue
                effect,source,engine_name=next(iter(matches.values()))
                engine_name=engine_name or a['name']
                evidence.append({'kind':'attack','text':a['text'],'effectKey':source,'mechanicHash':digest(effect)})
            base=attack_base(a,effect)
            if base is None or 'UNKNOWN' in a['cost'] or a.get('additionalEnergyCondition'):
                errors.append('attack-cost-damage');continue
            attacks.append({'name':engine_name,'cnName':a['name'],'cost':a['cost'],'damage':base,**({'text':a['text'],'mechanic':copy.deepcopy(effect)} if effect else {})})
        abilities=[]
        for a in f['abilities']:
            matches=self.abilities.get(norm(a['text']),{})
            reviewed=[r for r in self.reviewed if r['kind']=='ability' and r['ruleHash']==up['ruleHash'] and r['text']==a['text']]
            if len(reviewed)==1:
                r=reviewed[0]
                assert digest(r['definition'])==r['definitionHash']
                matches={r['definitionHash']:(r['definition'],r['effectKey'])}
            if len(matches)!=1:errors.append('ability:'+a['text']);continue
            effect,source=next(iter(matches.values()))
            abilities.append({'name':a['name'],**copy.deepcopy(effect),'text':a['text']})
            evidence.append({'kind':'ability','text':a['text'],'effectKey':source,'mechanicHash':digest(effect)})
        # A copied clause may give an attack an existing engine name. Bind
        # printed references (e.g. last turn's named attack) to that final name.
        attack_names={a['name'] for a in attacks}
        printed_names={norm(a['cnName']):a['name'] for a in attacks}
        def bind(value):
            if isinstance(value,dict):
                for key,item in list(value.items()):
                    if key=='attackName' or key=='name' and value.get('term')=='attack_last_turn':
                        if item not in attack_names:
                            if norm(item) in printed_names:
                                value[key]=printed_names[norm(item)]
                                evidence.append({'kind':'attack-name-binding','printed':item,'engine':value[key]})
                            else:errors.append('unbound-attack-reference:'+item)
                    else:bind(item)
            elif isinstance(value,list):
                for item in value:bind(item)
        for attack in attacks:bind(attack.get('mechanic'))
        if errors:return None,errors
        lineage=next(iter(lineage.values()))
        result={'effectKey':'P4P-CHS'+up['ruleHash'][:16].upper(),**lineage,'hp':f['hp'],'type':f['type'],'weakness':[f['weakness']] if f['weakness'] else [],'resistance':[f['resistance']] if f['resistance'] else [],'retreat':f['retreat'],'attacks':attacks,'printings':[],'sourceAdapter':'chs-reviewed-clauses-1','sourceRuleHash':up['ruleHash'],'sourceEvidence':evidence}
        if abilities:result['abilities']=abilities
        if f['resistance'] and f['resistanceFormula']=='-20':result['resistanceAmount']=20
        if f['pokemonType']=='宝可梦ex':result.update(pokemonType='EX',prize=2)
        if f['specialCard']:result['pokemonRule']={'太晶':'TERA','古代':'ANCIENT','未来':'FUTURE'}[f['specialCard']]
        return result,[]


def main():
    sha='ea69b4e3916a717ffe0c0116984c99d3e4a3cb8c'
    n=normalize(read(f'.catalog/sync/snapshots/{sha}/upstream.json'),corrections=read('data/sync/source-corrections.json'))
    compiler=Compiler(read('data/cardpool/plain-pokemon.json'),read('data/catalog/catalog.json'))
    rows=read('artifacts/sync/full-face-review.json')
    specs={};gaps=[];mappings=[]
    for row in rows:
        if len({x['effectKey'] for x in row['checks'] if not x['errors']})==1:continue
        up=n['cards'][row['cardId']]
        spec,errors=compiler.pokemon(up)
        if spec:
            specs[spec['effectKey']]=spec
            mappings.append({'printingId':row['printingId'],'cardId':up['id'],'effectKey':spec['effectKey'],'ruleHash':up['ruleHash']})
        else:gaps.append({**row,'errors':errors,'face':up['face']})
    write('artifacts/sync/compiled-review.json',{'specs':list(specs.values()),'mappings':mappings,'gaps':gaps})
    print('compiled',len(mappings),'printings',len(specs),'effects','remaining',len(gaps))
    print(Counter(e.split(':')[0] for r in gaps for e in r['errors']))


if __name__=='__main__':main()

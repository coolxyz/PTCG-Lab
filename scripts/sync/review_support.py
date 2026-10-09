"""Review complete source faces against existing compiled effects (no publication)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sync.common import read, write, digest
from packages.sync.normalize import normalize
from packages.sync.equivalence import text
from collections import defaultdict, Counter
from opencc import OpenCC
import re
from functools import lru_cache

ZH = OpenCC('t2s')

@lru_cache(maxsize=65536)
def norm(s):
    return text(ZH.convert(s or ''))

def name(s):
    return norm(s).replace(' ', '')


BOILERPLATES = {
    '物品': '在自己的回合可以使用任意张物品卡。',
    '支援者': '在自己的回合只可以使用1张支援者卡。',
    '竞技场': '在自己的回合，可以将1张竞技场卡放于战斗场旁。如果有别的竞技场卡被放于场上的话，则将此卡放于弃牌区。无法将同名的竞技场卡放于场上。',
    '宝可梦道具': '在自己的回合可以将任意张宝可梦道具卡，放于自己的宝可梦身上。每只宝可梦身上只可以放1张宝可梦道具卡，并保持附加状态。',
}


def trainer_text(f):
    value=norm(f['ruleText'])
    suffix=norm(BOILERPLATES.get(f['trainerType'],''))
    if suffix and value.endswith(suffix):
        value=value[:-len(suffix)].rstrip('|')
    return value

def pokemon_mismatches(f, s):
    errors = []
    for k in ('hp','type','stage','retreat'):
        if f[k] != s[k]: errors.append(k)
    for k in ('weakness','resistance'):
        if ([f[k]] if f[k] else []) != s[k]: errors.append(k)
    if f['weakness'] and f['weaknessFormula'] != '×2': errors.append('weaknessFormula')
    if f['resistance'] and f['resistanceFormula'] != '-'+str(s.get('resistanceAmount',30)): errors.append('resistanceFormula')
    if (f['pokemonType'] == '宝可梦ex') != (s.get('pokemonType') == 'EX'): errors.append('pokemonType')
    traits = {'太晶':'TERA','古代':'ANCIENT','未来':'FUTURE'}
    if traits.get(f['specialCard'], 'NONE') != s.get('pokemonRule','NONE'): errors.append('specialCard')
    if f['skills']: errors.append('skills')
    allowed_rule = '当宝可梦ex【昏厥】时，对手将拿取2张奖赏卡。' if f['pokemonType']=='宝可梦ex' else ''
    if norm(f['ruleText']) != norm(allowed_rule): errors.append('ruleText')
    if [norm(a['text']) for a in f['abilities']] != [norm(a.get('text','')) for a in s.get('abilities',[])]: errors.append('abilities')
    if len(f['attacks']) != len(s['attacks']): errors.append('attacks')
    else:
        for i,(a,b) in enumerate(zip(f['attacks'],s['attacks'])):
            if a['cost'] != b['cost']: errors.append(f'cost{i}')
            base_damage = 0 if a['damage'].endswith(('×','x')) else int(re.match(r'\d*', a['damage'])[0] or '0')
            if b.get('mechanic',{}).get('kind') in ('coin_count','coin_until_tails') and a['damage'].endswith(('×','x')):
                base_damage = int(re.match(r'\d*',a['damage'])[0] or '0')
            if base_damage != b['damage']: errors.append(f'damage{i}')
            if norm(a['text']) != norm(b.get('text','')): errors.append(f'text{i}')
            if a.get('additionalEnergyCondition'):errors.append(f'additional{i}')
    return errors


def main():
    sha='ea69b4e3916a717ffe0c0116984c99d3e4a3cb8c'
    n=normalize(read(f'.catalog/sync/snapshots/{sha}/upstream.json'), corrections=read('data/sync/source-corrections.json'))
    p=read('data/cardpool/plain-pokemon.json')
    index=defaultdict(list)
    catalog=read('data/catalog/catalog.json')
    bypid={c['printingId']:c for c in catalog['cards']}
    for s in p['cards']:
        names={name(bypid[pid]['cnName']) for pid in s['printings'] if pid in bypid}
        names.add(name(s.get('cardPage',s['name']).split('（')[0]))
        for cn in names:
            index[(cn,s['hp'],s['type'],s['stage'],tuple(name(a['cnName']) for a in s['attacks']))].append(s)
    # Scan current source faces, not a historical hand-picked gap report.
    a=[]
    seen=set()
    for card in catalog['cards']:
        for cid in card.get('upstreamFaces',{}):
            up=n['cards'][cid]
            if up['mark'] not in ('G','H','I','J') or up['ruleHash'] in seen:continue
            if not card.get('releasedAt') or card['releasedAt']>catalog['asOf']:continue
            seen.add(up['ruleHash'])
            a.append({'上游版本ID':cid,'卡牌ID':card['printingId']})
    rows=[]
    other=[]
    for row in a:
        c=n['cards'][row['上游版本ID'].split(',')[0]];f=c['face']
        if f['category']!='宝可梦':
            if f['category']=='训练家':
                types={'物品':'item','支援者':'supporter','竞技场':'stadium','宝可梦道具':'tool'}
                candidates=[]
                for s in p['trainers']:
                    names={name(bypid[pid]['cnName']) for pid in s['printings'] if pid in bypid}
                    names.add(name(s.get('cardPage',s['name']).split('（')[0]))
                    if s.get('sourceAdapter')=='chs-reviewed-wording-1':
                        names={name(s.get('cnName', s['name']))}
                    if name(f['name']) in names:
                        errors=[]
                        if s.get('mechanic',{}).get('kind')=='fossil':
                            if f!=s.get('sourceFace'):errors.append('fossil-face')
                        elif f['abilities'] or f['attacks'] or f['skills']:errors.append('extra-effects')
                        if trainer_text(f)!=norm(s['text']):errors.append('text')
                        if types.get(f['trainerType'])!=s['trainerType']:errors.append('trainerType')
                        if ('ACE SPEC' in (f['specialCard'] or '').split('|'))!=s.get('aceSpec',False):errors.append('aceSpec')
                        if {'古代':'ANCIENT','未来':'FUTURE'}.get((f['specialCard'] or '').split('|')[0])!=s.get('trait'):errors.append('trait')
                        candidates.append({'effectKey':s['effectKey'],'errors':errors})
                other.append({'cardId':c['id'],'printingId':row['卡牌ID'],'name':c['name'],'ruleHash':c['ruleHash'],'checks':candidates})
            continue
        candidates=index.get((name(f['name']),f['hp'],f['type'],f['stage'],tuple(name(a['name']) for a in f['attacks'])),[])
        checks=[{'effectKey':s['effectKey'],'errors':pokemon_mismatches(f,s)} for s in candidates]
        rows.append({'cardId':c['id'],'printingId':row['卡牌ID'],'name':c['name'],'ruleHash':c['ruleHash'],'checks':checks})
    write('artifacts/sync/full-face-review.json',rows)
    write('artifacts/sync/trainer-face-review.json',other)
    print(Counter('matched' if any(not x['errors'] for x in r['checks']) else 'differences' if r['checks'] else 'missing' for r in rows))
    print(Counter(e for r in rows for x in r['checks'] for e in x['errors']))
    print('trainers',Counter('matched' if any(not x['errors'] for x in r['checks']) else 'differences' if r['checks'] else 'missing' for r in other))


if __name__=='__main__':main()

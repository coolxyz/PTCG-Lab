"""Materialize reviewed trainer parameterizations, retaining source identity."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sync.common import read, write, digest
from packages.sync.normalize import normalize
from scripts.sync.review_support import trainer_text


def main():
    n=normalize(read('.catalog/sync/snapshots/ea69b4e3916a717ffe0c0116984c99d3e4a3cb8c/upstream.json'), corrections=read('data/sync/source-corrections.json'))
    p=read('data/cardpool/plain-pokemon.json')
    definitions={
        '12419': ('Youngster', {'kind':'shuffle_draw','count':5}),
        '12618': ('Youngster', {'kind':'shuffle_draw','count':5}),
        '16857': ('Picnicker', {'kind':'draw','count':2,'coinBonus':2}),
        '18757': ('Miracle Headset', {'kind':'recover_hand','count':2,'filter':'supporter'}),
        '18776': ('Jubilant Stadium', {'kind':'stadium','stage':'BASIC','hp':30}),
        '19503': ('Treasure Tracker', {'kind':'search_hand','count':5,'filter':'tool'}),
        '20155': ('MC’s Hype Up', {'kind':'draw','count':2,'bonus':2,'condition':'opponent_three_prizes'}),
    }
    for cid,(english,mechanic) in definitions.items():
        c=n['cards'][cid];f=c['face'];key='P4T-REUSE'+c['ruleHash'][:16].upper()
        spec={'effectKey':key,'name':english,'cardPage':f['name'],'trainerType':{'物品':'item','支援者':'supporter','竞技场':'stadium'}[f['trainerType']], 'text':trainer_text(f),'mechanic':mechanic,'printings':[],'aceSpec':f['specialCard']=='ACE SPEC','sourceRuleHash':c['ruleHash'],'sourceAdapter':'chs-reviewed-parameters-1'}
        old=next((s for s in p['trainers'] if s['effectKey']==key),None)
        if old:spec['printings']=old['printings'];p['trainers'].remove(old)
        p['trainers'].append(spec)
    specs={s['effectKey']:s for s in p['trainers']+p['specialEnergies']}
    reviews=read('data/sync/reviewed-wording.json')
    # Only punctuation/wording changes, with full printed semantics preserved.
    for cid,key in {'12581':'P4T-9CC9B1E5E3D2','12583':'P4T-9FBD27B31A88','21287':'P4T-9FBD27B31A88','12786':'P4S-26604AD8FF13','14777':'P4T-DDB79F5FA206','15256':'P4T-0BFE35A8366E'}.items():
        c=n['cards'][cid]
        reviews['cards']=[r for r in reviews['cards'] if r['cardId']!=cid]
        reviews['cards'].append({'cardId':cid,'effectKey':key,'ruleHash':c['ruleHash'],'sourceHash':c['sourceHash'],'specHash':digest(specs[key]),'reviewedAt':'2026-10-07','review':'逐句核对：选择范围、费用、数量和条件相同，仅标点、模板或动词措辞差异。'})
    write('data/sync/reviewed-wording.json',reviews)
    write('data/cardpool/plain-pokemon.json',p)


if __name__=='__main__':main()

"""Explicitly reviewed parameter changes; never promote fuzzy text matches."""
from pathlib import Path
import copy
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sync.common import read, write, digest


def main():
    candidates = read('artifacts/sync/reuse-clause-candidates.json')
    reviewed = read('data/sync/reviewed-clauses.json')
    changes = {
        ('15661', 'attack'): {'kind':'field_operation','operation':'delayed','effect':{'knockout':True}},
        ('21247', 'attack'): {'kind':'field_operation','operation':'transfer_counters','all':True},
        ('11508', 'attack'): {'kind':'bench_damage','amount':90,'damaged':True},
        ('20765', 'attack'): {'kind':'damage_expression','term':'attack_last_turn','name':'充斥瓦斯','factor':120,'mode':'add'},
        ('21249', 'attack'): {'kind':'field_operation','operation':'timed_modifier','key':'龙卷风突进','self':True,'nextOwnTurn':True,'modifier':{'attackName':'龙卷风突进','damage':100}},
        ('11512', 'ability'): {'kind': 'continuous', 'retreatFree': True, 'trigger': 'passive', 'usageLimit': 'unlimited', 'activeZones': ['active', 'bench']},
        ('12309', 'attack'): {'kind': 'coin_count', 'countTerm': 'self_typed_energy', 'type': 'METAL', 'perHead': 80},
        ('21883', 'attack'): {'kind': 'damage_expression', 'term': 'any_stadium', 'mode': 'add', 'factor': 70},
        ('20891', 'attack'): {'kind': 'staged_attack', 'after': True, 'cost': {'kind': 'hand'}, 'effects': [{'kind': 'draw', 'count': 3}]},
        ('20756', 'attack'): {'kind': 'staged_attack', 'top': 3, 'filter': 'energy', 'factor': 80, 'mode': 'multiply'},
        ('14278', 'attack'): {'kind': 'target_damage', 'zone': 'all', 'count': 1, 'amount': 220, 'discardEnergy': 2, 'discardType': 'LIGHTNING'},
        ('11509', 'attack'): {'kind': 'target_damage', 'zone': 'bench', 'count': 1, 'amount': 120, 'discardEnergy': 2, 'discardType': 'FIRE'},
        ('16976', 'attack'): {'kind': 'coin_branch', 'flips': 3, 'perHeadEffect': {'kind': 'attach_multiple', 'count': 1, 'basic': True, 'type': 'LIGHTNING', 'origin': 'discard', 'target': 'bench'}},
        ('21667', 'attack'): {'kind': 'coin_branch', 'flips': 'until_tails', 'perHeadEffect': {'kind': 'attach_multiple', 'count': 1, 'basic': True, 'type': 'LIGHTNING', 'origin': 'left', 'target': 'self'}},
        ('15669', 'attack'): {'kind': 'sequence', 'steps': [{'kind': 'recoil', 'amount': 90}, {'kind': 'special_status', 'status': 'BURNED', 'coin': False, 'target': 'opponent'}]},
        ('21894', 'attack'): {'kind': 'attach_multiple', 'count': 1, 'basic': True, 'type': 'FIRE', 'origin': 'discard', 'target': 'all', 'targetType': 'DRAGON', 'mandatory': True},
        ('21952', 'ability'): {'kind': 'retaliate', 'counters': 30, 'activeOnly': True, 'activeZones': ['active'], 'trigger': 'passive', 'usageLimit': 'unlimited'},
        ('21127', 'attack'): {'kind': 'damage_expression', 'term': 'bench_names', 'names': ['Beldum', 'Metang'], 'factor': 150, 'mode': 'add'},
        ('11487', 'attack'): {'kind': 'field_operation', 'operation': 'conditional_effects', 'condition': {'term': 'has_status', 'owner': 'opponent', 'status': 'ASLEEP'}, 'effects': [{'kind': 'field_operation', 'operation': 'knockout'}]},
    }
    for key, definition in changes.items():
        matches = [c for c in candidates if (c['cardId'], c['kind']) == key]
        if key==('21247','attack') and not matches:
            n=read('.catalog/sync/snapshots/ea69b4e3916a717ffe0c0116984c99d3e4a3cb8c/normalized.json')['cards'][key[0]]
            attacks=[a for a in n['face']['attacks'] if a['text'].startswith('选择自己的1只备战宝可梦，将被选择的宝可梦身上放置的所有伤害指示物')]
            assert len(attacks)==1
            matches=[{'ruleHash':n['ruleHash'],'kind':'attack','text':attacks[0]['text'],'effectKey':'packages.rules.transfer_attacks','existingText':'transfer_counters: own bench donor, all counters to opposing Active'}]
        assert len(matches) == 1, key
        c = matches[0]
        row = {'kind': c['kind'], 'ruleHash': c['ruleHash'], 'text': c['text'], 'effectKey': c['effectKey'], 'definition': copy.deepcopy(definition), 'definitionHash': digest(definition), 'review': '完整条文逐项核对；复用现有处理器支持的条件、目标、数量、时机参数。', 'sourceText': c['existingText']}
        reviewed['clauses'] = [r for r in reviewed['clauses'] if (r['ruleHash'], r['kind'], r['text']) != (row['ruleHash'], row['kind'], row['text'])]
        reviewed['clauses'].append(row)
    write('data/sync/reviewed-clauses.json', reviewed)
    # An earlier candidate with absent printed retreat must not be registered.
    plain = read('data/cardpool/plain-pokemon.json')
    plain['cards'] = [s for s in plain['cards'] if s['effectKey'] != 'P4P-CHS25D20CE634F534F7']
    write('data/cardpool/plain-pokemon.json', plain)
    print('reviewed', len(changes))


if __name__ == '__main__':
    main()

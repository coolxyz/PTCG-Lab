"""Bind the reviewed November 2026 Chinese faces to executable candidates."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.sync.common import digest, read, write
from packages.sync.normalize import normalize
from scripts.sync.review_support import trainer_text

COMMIT = 'b5164fb7e6fabfd6210d485940635ff6e05e0d62'
UPSTREAM = 'https://github.com/duanxr/PTCG-CHS-Datasets'
SOURCE_DIGEST = '3ef908453a5737430ace96dbe537630617a6c74c99a71304019064f661d61edc'


def rule(kind, **fields):
    return {'kind': kind, **fields}


def field(operation, **fields):
    return rule('field_operation', operation=operation, **fields)


def damage(term, factor, mode='add', **fields):
    return rule('damage_expression', term=term, factor=factor, mode=mode, **fields)


def sequence(*steps):
    return rule('sequence', steps=list(steps))


def status(*statuses):
    return rule('special_status', target='opponent', coin=False, status=statuses[0], statuses=list(statuses))


def conditional(term, **fields):
    return rule('conditional_attack', condition={'term': term}, **fields)


def timed_damage(name, amount):
    return field('timed_modifier', key=name, self=True, nextOwnTurn=True, modifier={'attackName': name, 'damage': amount})


def activated(effect, **fields):
    return rule('activated_effect', trigger='activated', effect=effect, **fields)


def passive(kind, **fields):
    return rule(kind, trigger='passive', **fields)


ATTACKS = {
    '22367': {0: rule('attack_protection', coin=True, damage=True)},
    '22368': {0: sequence(status('POISONED', 'CONFUSED'), rule('self_switch', optional=False))},
    '22370': {1: damage('target_type', 100, type='DARK')},
    '22450': {1: damage('played_supporter_prefix', 100, prefix="Team Rocket's ")},
    '22458': {0: rule('search_hand', count=3, filter='any', reveal=False)},
    '22462': {0: rule('coin_branch', flips=1, heads=[status('POISONED', 'PARALYZED')], tails=[status('CONFUSED')])},
    '22463': {0: damage('healed_this_turn', 100)},
    '22467': {0: rule('conditional_attack', condition={'term': 'own_bench', 'atLeast': 5})},
    '22472': {0: damage('discard_typed_basic_energy', 100, type='FIRE', minimum=10)},
    '22477': {0: damage('field_attack_name', 20, 'multiply', name='轮唱', englishName='Round')},
    '22478': {0: damage('field_attack_name', 40, 'multiply', name='轮唱', englishName='Round')},
    '22479': {0: damage('field_attack_name', 70, 'multiply', name='轮唱', englishName='Round')},
    '22480': {0: damage('discard_item_count', 30, 'multiply')},
    '22483': {0: rule('opponent_attack_lock')},
    '22484': {1: rule('opponent_attack_lock')},
    '22485': {0: rule('gust_damage', amount=20)},
    '22486': {1: rule('bench_damage', amount=10, all=True, perOpponentPrize=True)},
    '22492': {1: sequence(damage('opponent_prizes_taken', 50), rule('recoil', amount=30))},
    '22494': {0: rule('search_hand', count=60, filter='name:Fennel')},
    '22496': {0: field('evolve_field', searchEvolutionFirst=True)},
    '22497': {1: damage('field_matching', 40, evolved=True)},
    '22498': {0: field('move_field_energy')},
    '22502': {0: timed_damage('回声', 80)},
    '22504': {0: rule('bench_damage', amount=60, damaged=True)},
    '22508': {0: damage('opponent_retreat', 30, 'subtract')},
    '22511': {0: rule('attach_multiple', origin='discard', basic=True, type='FIGHTING', target='self', count=1, mandatory=True),
              1: rule('own_bench_damage', amount=10)},
    '22513': {0: damage('has_status', 60, owner='opponent', status='POISONED')},
    '22514': {0: damage('has_status', 90, owner='opponent', status='POISONED')},
    '22525': {0: rule('search_hand', count=2, filter='basic_energy')},
    '22527': {1: field('knockout', basic=True)},
    '22529': {0: rule('coin_branch', flips=1, failZero=True, heads=[rule('attack_protection', coin=False, damage=True, effects=True)])},
    '22530': {0: rule('draw', count=4)},
    '22792': {0: rule('heal_field', target='all', stage='BASIC', amount=100)},
    '22794': {0: rule('attach_multiple', origin='left', basic=True, count=3)},
    '22805': {0: rule('search_hand', count=3, filter='either:pokemon:FIRE|basic_energy:FIRE')},
    '22806': {1: sequence(damage('opponent_prizes_taken', 50), rule('discard_self_energy', count=1))},
    '22809': {0: damage('self_energy', 50)},
    '22811': {0: rule('discard_opponent_energy', count=1, type='FIRE')},
    '22815': {1: rule('coin_branch', flips=2, perHead=90, heads=[status('PARALYZED')])},
    '22816': {0: damage('moved_this_turn', 90)},
    '22820': {0: rule('discard_damage', zone='self', type='LIGHTNING', mandatoryAll=True, mode='multiply', factor=50)},
    '22821': {0: damage('self_has_typed_energy', 20, type='FIGHTING')},
    '22823': {0: field('return_own_bench', destination='hand')},
    '22824': {0: rule('damage_shield', amount=40),
              1: rule('target_damage', zone='all', count=1, amount=70, ignoreWeaknessResistance=True)},
    '22826': {0: field('transfer_counters', all=True, chooseRecipient=True)},
    '22831': {0: damage('extra_attack_energy', 80, number=2)},
    '22832': {0: rule('damage_shield', maximum=40, amount=0)},
    '22833': {0: damage('target_resistance', 50, type='FIGHTING')},
    '22834': {0: damage('bench_typed_counters', 20, 'multiply', type='FIGHTING')},
    '22835': {1: conditional('opponent_ex', ignoreWeaknessResistance=True)},
    '22840': {0: damage('knockout_last_turn', 80)},
    '22841': {0: rule('search_hand', count=3, filter='pokemon:DARK')},
    '22844': {0: rule('coin_count', countTerm='typed_pokemon', type='DARK', zone='field', perHead=60)},
    '22848': {0: damage('opponent_bench', 30, 'multiply')},
    '22853': {0: rule('target_damage', zone='all', count=1, amount=20, term='self_energy')},
    '22857': {0: damage('own_bench_named', 20, name='Durant')},
    '22860': {0: timed_damage('终结门牙', 160), 1: rule('coin_fail')},
    '22863': {0: rule('coin_branch', flips=3, perHeadEffect=rule('recover_hand', filter='any', count=1, optional=True)),
              1: damage('self_has_special_energy', 100)},
    '22867': {0: rule('attach_multiple', origin='hand', basic=True, target='self', count=1, mandatory=True),
              1: field('transfer_energy', fromSelf=True, basic=True, count=1)},
}

ABILITIES = {
    '22458': passive('continuous', scope='team', damage=20),
    '22464': passive('evolution_permission', partner='Shelmet'),
    '22474': activated(rule('status', status='BURNED'), discardBasicType='FIRE'),
    '22481': passive('protection', source='special_energy', damage=True, effects=True),
    '22482': activated(rule('expanded_entry', op='deploy_opponent', fromDiscard=True, maximumHP=70, count=1, required=True), activeOnly=True),
    '22489': activated(rule('attach', origin='discard', type='LIGHTNING', target='bench', basic=True, count=1, mandatory=True)),
    '22502': passive('first_turn_attack'),
    '22507': passive('continuous', scope='self', hpPerEnergy={'type': 'FIGHTING', 'amount': 40}),
    '22514': passive('retaliate', status='POISONED'),
    '22519': activated(rule('expanded_entry', op='deploy_opponent', maximumHP=70, count=1, required=True)),
    '22524': activated(rule('search', filter='evolved:METAL', count=2)),
    '22791': activated(rule('heal', target='active', amount=20)),
    '22797': passive('evolution_permission', partner='Karrablast'),
    '22802': activated(rule('attach', origin='hand', type='FIRE', basic=True, count=1, mandatory=True), usageLimit='unlimited'),
    '22809': activated(rule('expanded_entry', op='switch_both')),
    '22829': activated(rule('expanded_entry', op='opponent_redraw', count=3), activeOnly=True),
    '22831': passive('hand_lock', activeOnly=True, cards='item_and_tool'),
    '22837': activated(rule('expanded_entry', op='devolve_opponent'), activeOnly=True),
    '22851': passive('attack_knockout_prize', count=1),
    '22856': passive('continuous', scope='team', energy='METAL', armor=20),
}

LINEAGE = {
    '22368': ('Accelgor', 'Shelmet'),
    '22450': ("Team Rocket's Kangaskhan ex", None),
    '22457': ('Servine', 'Snivy'),
    '22458': ('Serperior ex', 'Servine'),
    '22494': ('Musharna', 'Munna'),
    '22504': ('Excadrill ex', 'Drilbur'),
    '22513': ('Whirlipede', 'Venipede'),
    '22519': ('Mandibuzz', 'Vullaby'),
    '22791': ('Swadloon', 'Sewaddle'),
    '22792': ('Leavanny', 'Swadloon'),
    '22794': ('Whimsicott ex', 'Cottonee'),
    '22798': ('Accelgor', 'Shelmet'),
    '22801': ('Pignite', 'Tepig'),
    '22802': ('Emboar', 'Pignite'),
    '22808': ('Dewott', 'Oshawott'),
    '22809': ('Samurott', 'Dewott'),
    '22831': ('Jellicent ex', 'Frillish'),
    '22833': ('Boldore', 'Roggenrola'),
    '22834': ('Gigalith', 'Boldore'),
    '22836': ('Archen', 'Antique Plume Fossil'),
    '22837': ('Archeops', 'Archen'),
    '22860': ('Watchog', 'Patrat'),
}

TRAINERS = {
    '22393': ("Team Rocket's Hypnotizer", rule('tool_modifier', condition='rocket', reaction=rule('retaliate', status='ASLEEP'))),
    '22451': ('Tool Scrapper', rule('trainer_operation', op='strip_tools', count=2)),
    '22453': ('Air Balloon', rule('tool_modifier', retreatLess=2)),
    '22454': ('Hilda', rule('search_pair', filters=['evolution', 'energy'], count=1)),
    '22534': ('Energy Coin', rule('trainer_operation', op='double_heads_attach')),
    '22538': ("N's Plan", rule('trainer_operation', op='bench_energy_to_active', count=2)),
    '22540': ('Fennel', rule('heal_own_all', count=40)),
    '22870': ('Antique Plume Fossil', rule('fossil')),
    '22871': ('Brave Bangle', rule('tool_modifier', condition='no_rule_box', target='ex', damage=30)),
    '22872': ('Harlequin', rule('trainer_operation', op='coin_redraw_both')),
    '22873': ('Cheren', rule('draw', count=3)),
}

REUSE = {
    '22365': 'P4T-C47627A959F1',  # Same concealed selections and optional exchange.
    '22452': 'P4T-247A487E8276',
    '22539': 'P4T-CHS2C196BC94A37023F',
    '22868': 'P4T-70A87B01963B',
}

ENERGIES = {
    '22455': ('Prism Energy', rule('special_energy', provides='COLORLESS', basicWildcard=True)),
    '22875': ('Ignition Energy', rule('special_energy', provides='COLORLESS', evolvedUnits=3, discardAtOwnTurnEnd=True)),
}


def prepare(root, snapshot):
    source = read(snapshot / 'source.json')
    if source['commit'] != COMMIT or source['repository'] != UPSTREAM:
        raise ValueError('November bindings require the reviewed upstream snapshot')
    corrections_path = root / 'data/sync/source-corrections.json'
    corrections = read(corrections_path)
    raw = read(snapshot / 'upstream.json')
    if digest(raw) != SOURCE_DIGEST:
        raise ValueError('November card source digest does not match the reviewed input')
    tree = source['tree']
    original = normalize(raw, tree)
    druddigon = original['cards']['22858']
    if druddigon['sourceHash'] != 'c2a1c754d8b12ebb01da73250c1c30a2551fda08434b5748598bb3182c2b0fc7':
        raise ValueError('Druddigon source evidence changed')
    for card_id, card in original['cards'].items():
        if card['ruleHash'] == druddigon['ruleHash']:
            corrections['cards'][card_id] = {
                'sourceHash': card['sourceHash'], 'set': {'weaknessType': None},
                'evidence': f'{UPSTREAM}/blob/{COMMIT}/{druddigon["imagePath"]}',
            }
    farigiraf = original['cards']['22389']
    corrections['cards']['22389'] = {
        'sourceHash': farigiraf['sourceHash'], 'set': {},
        'attackCosts': [{'index': 0, 'before': '9,11,11', 'after': '5,11,11'}],
        'evidence': f'{UPSTREAM}/blob/{COMMIT}/{farigiraf["imagePath"]}',
    }
    normalized = normalize(raw, tree, corrections)['cards']
    clauses_path = root / 'data/sync/reviewed-clauses.json'
    evolution_path = root / 'data/sync/evolution-evidence.json'
    specs_path = root / 'data/cardpool/plain-pokemon.json'
    effects_path = root / 'data/simulation/effects.json'
    proofs_path = root / 'data/sync/reviewed-equivalence.json'
    clauses, evolution, specs, effects, proofs = map(read, (clauses_path, evolution_path, specs_path, effects_path, proofs_path))
    for card_id, (name, predecessor) in LINEAGE.items():
        card = normalized[card_id]
        evolution['cards'] = [r for r in evolution['cards'] if (r['name'], r['stage']) != (card['name'], card['face']['stage'])]
        evolution['cards'].append({'name': card['name'], 'stage': card['face']['stage'], 'englishName': name,
                                   'evolvesFrom': [predecessor] if predecessor else [],
                                   'evidence': f'{UPSTREAM}/blob/{COMMIT}/{card["imagePath"]}'})
    for kind, bindings in [('attack', ATTACKS), ('ability', {cid: {0: value} for cid, value in ABILITIES.items()})]:
        for card_id, definitions in bindings.items():
            card = normalized[card_id]
            for i, definition in definitions.items():
                text = card['face']['attacks' if kind == 'attack' else 'abilities'][i]['text']
                clauses['clauses'] = [r for r in clauses['clauses'] if (r['kind'], r['ruleHash'], r['text']) != (kind, card['ruleHash'], text)]
                clauses['clauses'].append({'kind': kind, 'ruleHash': card['ruleHash'], 'text': text, 'definition': definition,
                                           'definitionHash': digest(definition), 'effectKey': 'november-2026-reviewed'})
    registry = {r['effectKey']: r for r in effects['effects']}
    for card_id in sorted(TRAINERS.keys() | ENERGIES.keys() | REUSE.keys()):
        card = normalized[card_id]
        face = card['face']
        if card_id in REUSE:
            key = REUSE[card_id]
            if key not in registry:
                raise ValueError(f'Missing existing effect: {key}')
        else:
            special = card_id in ENERGIES
            name, mechanic = (ENERGIES if special else TRAINERS)[card_id]
            key = ('P4S-' if special else 'P4T-') + 'NOV' + card['ruleHash'][:16].upper()
            spec = {'effectKey': key, 'name': name, 'cardPage': face['name'], 'printings': [], 'aceSpec': False,
                    'text': face['ruleText'] if special else trainer_text(face), 'mechanic': mechanic,
                    'sourceRuleHash': card['ruleHash']}
            if special:
                spec['energyType'] = 'special'
            else:
                spec['trainerType'] = {'物品': 'item', '支援者': 'supporter', '宝可梦道具': 'tool'}[face['trainerType']]
                if card_id == '22870':
                    spec['abilities'] = [{'name': '羽毛守护', 'text': face['ruleText'].split('|')[1],
                                          **passive('protection', zone='bench', damage=True)}]
            group = 'specialEnergies' if special else 'trainers'
            specs[group] = [s for s in specs[group] if s['effectKey'] != key] + [spec]
            registry[key] = {'effectKey': key, 'name': name, 'printings': [], 'status': 'experimental', 'registered': True,
                             'mechanisms': [mechanic['kind']], 'implementation': 'packages.rules.special_energy' if special else 'packages.rules.trainers',
                             'testSuites': ['tests/cardpool/test_november_2026.py', 'tests/simulation/test_prompt_matrix.py'],
                             'rulesRevision': '2026-11-09', 'scope': 'november-2026-reviewed'}
        proofs['proofs'][card['ruleHash']] = {'effectKey': key, 'face': face, 'cardId': card_id, 'sourceHash': card['sourceHash'],
                                             'commit': COMMIT, 'method': 'november-complete-face-review'}
        if card_id == '22539':
            proofs['proofs'][card['ruleHash']]['nameLimitKey'] = '博士的研究'
    effects['effects'] = list(registry.values())
    return dict(zip((corrections_path, clauses_path, evolution_path, specs_path, effects_path, proofs_path),
                    (corrections, clauses, evolution, specs, effects, proofs), strict=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    files = prepare(args.assets, args.snapshot)
    args.output.mkdir(parents=True, exist_ok=False)
    for path, value in files.items():
        write(args.output / path.relative_to(args.assets), value)


if __name__ == '__main__':
    main()

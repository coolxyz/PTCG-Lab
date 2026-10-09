import copy

import pytest

from packages.sync.common import SyncError, digest
from packages.sync.normalize import normalize
from packages.sync.identity import build_catalog
from tests.sync.test_sync import raw


def test_rule_unknown_does_not_hide_identity_bound_image(raw):
    raw['collections'][0]['cards'][0]['details']['pokemonType'] = '999'
    n = normalize(raw, {'img/1/0.png': 'abc'})
    def build():
        return build_catalog(n, {'cards': [], 'products': []}, {'cards': {}}, 'a'*40)['catalog']['cards'][0]
    assert build()['images']
    assert build()['effectStatus'] == 'unverified'
    n['cards']['1']['issues'].append('IDENTITY_CONFLICT')
    assert not build()['images']


def test_source_correction_bound_to_raw_and_keeps_provenance(raw):
    d = raw['collections'][0]['cards'][0]['details']
    d['pokemonType'] = '12'
    original = copy.deepcopy(d)
    patch = {'cards': {'1': {'sourceHash': digest(d), 'set': {'pokemonType': None}, 'evidence': {'image': 'reviewed'}}}}
    c = normalize(raw, corrections=patch)['cards']['1']
    assert c['raw'] == original and c['sourceHash'] == digest(original)
    assert c['face']['pokemonType'] is None and not c['issues']
    d['hp'] += 10
    with pytest.raises(SyncError, match='上游内容已变化'):
        normalize(raw, corrections=patch)


def test_user_confirmed_retreat_and_attack_text_preserve_raw(raw):
    d = raw['collections'][0]['cards'][0]['details']
    before = d['abilityItemList'][0]['abilityText']
    original = copy.deepcopy(raw)
    correction = {'sourceHash': digest(d), 'set': {'retreatCost': 1},
                  'attackTexts': [{'index': 0, 'before': before, 'after': '选择1个能量。'}],
                  'evidence': {'reviewer': 'user'}}
    patches = {'cards': {'1': correction}}
    result = normalize(raw, corrections=patches)['cards']['1']
    assert result['face']['retreat'] == 1
    assert result['face']['attacks'][0]['text'] == '选择1个能量。'
    assert raw == original and result['raw'] == d
    correction['attackTexts'][0]['before'] = '错误原文'
    with pytest.raises(SyncError, match='招式文字修正'):
        normalize(raw, corrections=patches)
    correction['attackTexts'] = []
    correction['set']['retreatCost'] = -1
    with pytest.raises(SyncError, match='撤退费用'):
        normalize(raw, corrections=patches)


def test_multivalue_enums_and_additional_cost_are_lossless(raw):
    raw['dict']['pokemon_type'] = [{'dictCode': '1', 'dictValue': '宝可梦V'}, {'dictCode': '2', 'dictValue': '宝可梦VMAX'}]
    raw['dict']['special_card'] = [{'dictCode': '7', 'dictValue': '古代'}, {'dictCode': '9', 'dictValue': 'ACE SPEC'}]
    d = raw['collections'][0]['cards'][0]['details']
    d.update(pokemonType='1|2', specialCard='7,9')
    d['abilityItemList'][0]['abilityCost'] = 'none,12'
    c = normalize(raw)['cards']['1']
    assert not c['issues']
    assert c['face']['pokemonType'] == '宝可梦V|宝可梦VMAX'
    assert c['face']['specialCard'] == '古代|ACE SPEC'
    assert c['face']['attacks'][0]['cost'] == []
    assert c['face']['attacks'][0]['additionalEnergyCondition']


def test_basic_energy_metadata_does_not_grant_battle_support(raw):
    raw['dict']['card_type'].append({'dictCode': '3', 'dictValue': '能量'})
    raw['dict']['energy_type'] = [{'dictCode': '1', 'dictValue': '基本能量'}]
    raw['collections'][0]['cards'][0]['details'].update(cardType='3', energyType='1')
    m = build_catalog(normalize(raw), {'cards': [], 'products': []}, {'cards': {}}, 'a'*40)
    c = m['catalog']['cards'][0]
    assert c['basicEnergyType'] == 'grass'
    assert not c['sourceVerified'] and c['effectStatus'] == 'unverified'

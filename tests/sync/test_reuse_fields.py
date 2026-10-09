import copy
from packages.sync.adapt import adapt, enable_proposals


def test_reviewed_full_face_repairs_category_and_energy_without_mutating_baseline():
    card = {"printingId":"test", "category":"宝可梦", "mark":None, "releasedAt":"2026-01-01", "sourceEvidence":[], "effectStatus":"unverified"}
    face = {"category":"能量", "energyType":"基本能量", "type":"FIRE", "hp":None, "stage":None, "trainerType":None, "pokemonType":None, "attacks":[], "specialCard":None}
    migration = {"catalog":{"cards":[card]}, "mappings":{"1":{"printingId":"test","status":"matched"}}}
    source = {"cards":{"1":{"face":face,"mark":None,"issues":[],"ruleHash":"hash","name":"基本火能量"}}}
    effects = {"effects":[{"effectKey":"SVE-002","name":"Fire Energy","status":"experimental","printings":[]}]}
    plain = {"cards":[]}
    scope = {"allowedMarks":list("GHIJ"),"basicEnergyTypes":["fire"]}
    implementation = {"1":{"ruleHash":"hash","effectKey":"SVE-002","engineLine":"Fire Energy SVE 002","testFiles":[__file__]}}
    before = copy.deepcopy(migration)
    result = adapt(migration, source, effects, plain, scope, scope, "2026-10-07", implementations=implementation)
    candidate, registered, _ = enable_proposals(migration['catalog'], result)
    assert candidate['cards'][0]['category']=='能量'
    assert candidate['cards'][0]['basicEnergyType']=='fire'
    assert candidate['cards'][0]['effectStatus']=='verified'
    assert registered['effects'][0]['printings']==['test']
    assert card==before['catalog']['cards'][0]
    implementation['1']['ruleHash']='different'
    rejected=adapt(before,source,effects,plain,scope,scope,"2026-10-07",implementations=implementation)
    assert not rejected['proposals']


def test_rebinding_a_reviewed_face_removes_the_previous_live_alias():
    catalog = {"cards": [{"printingId": "p", "engineId": "old", "sourceEvidence": []}]}
    result = {"effects": {"effects": [
        {"effectKey": "old", "name": "Old", "printings": ["p"]},
        {"effectKey": "new", "name": "New", "printings": []},
    ]}, "plain": {"cards": [{"effectKey": "old", "printings": ["p"]}]},
        "proposals": {"p": {"effectKey": "new", "engineLine": "New TEST 001"}}}
    candidate, effects, plain = enable_proposals(catalog, result)
    assert candidate['cards'][0]['engineId'] == 'new'
    assert effects['effects'][0]['printings'] == []
    assert effects['effects'][1]['printings'] == ['p']
    assert plain['cards'][0]['printings'] == []
    assert result['effects']['effects'][0]['printings'] == ['p']

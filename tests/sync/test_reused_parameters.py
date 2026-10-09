"""Exercise reviewed compositions with adversarial targets, not just flags."""
import json
import random
import pytest
from pathlib import Path
from ptcg.core.card_registry import registry
from ptcg.core.enums import SpecialCondition, CardType
from tests.cardpool.test_field_effects import field_fixture
from test_effects import drive, zone


@pytest.mark.parametrize('lower_id,upper_id', [
    ('CN:151C:013', 'CN:151C:014'),
    ('CN:CSV10C:153', 'CN:CHS:21888'),
    ('CN:CHS:18017', 'CN:CHS:18018'),
    ('CN:CHS:18049', 'CN:CHS:18050'),
    ('CN:CHS:21113', 'CN:CHS:21114'),
])
def test_source_compiled_basic_can_evolve_into_reused_english_named_card(lower_id, upper_id):
    from packages.collection.domain import CARDS
    from tests.cardpool.test_checkup import board
    from ptcg.utils.utils import check_evolve
    s,p,o=board()
    lower=registry.get(CARDS[lower_id]['engineId'])()
    upper=registry.get(CARDS[upper_id]['engineId'])()
    zone(p,'active',[lower]);lower.firstTurnPlayed=False
    zone(p,'bench',[])
    assert check_evolve(upper,s)==[lower]
    lower.firstTurnPlayed=True
    assert check_evolve(upper,s)==[]


def rule(cid):
    source=json.loads(Path('.catalog/sync/snapshots/ea69b4e3916a717ffe0c0116984c99d3e4a3cb8c/normalized.json').read_text('utf8'))
    h=source['cards'][cid]['ruleHash']
    clauses=json.loads(Path('data/sync/reviewed-clauses.json').read_text('utf8'))['clauses']
    return next(c['definition'] for c in clauses if c['ruleHash']==h and c['kind']=='attack')


def test_metal_coin_count_ignores_other_energy():
    r=rule('12309');s,p,o,c,t,a=field_fixture(r)
    c.energy=[CardType.METAL,CardType.METAL,CardType.FIRE]
    s.rng=random.Random(1);t.hp=1000
    from ptcg.utils.utils import flip_coin
    from ptcg.core.enums import Coin
    heads=sum(flip_coin(s)==Coin.HEAD for _ in range(2))
    s.rng=random.Random(1)
    drive(c.reduce_action(a,s))
    assert t.hp==1000-heads*80


def test_quagsire_counts_only_own_top_three_energy():
    r=rule('20756');s,p,o,c,t,a=field_fixture(r)
    cards=[registry.get('SVE-008')(), registry.get('MEW-151')(), registry.get('SVE-002')()]
    zone(p,'left',cards+[registry.get('MEW-151')()]);t.hp=1000
    opponent_top=list(o.left)
    drive(c.reduce_action(a,s))
    assert t.hp==840 and all(e in p.discard for e in cards)
    assert o.discard==[]


def test_jynx_knockout_requires_sleep():
    for asleep in (False,True):
        r=rule('11487');s,p,o,c,t,a=field_fixture(r)
        t.hp=1000;t.special_condition=SpecialCondition.ASLEEP if asleep else SpecialCondition.NONE
        from packages.rules.remaining_attacks import resolve
        drive(resolve(r,a,s))
        assert t.hp==(0 if asleep else 1000)


def test_druddigon_attachment_excludes_non_dragon():
    r=rule('21894');s,p,o,c,t,a=field_fixture(r)
    from packages.rules.attachment_effects import recipients
    c.cardType=CardType.FIRE
    dragon=registry.get('MEW-151')();dragon.cardType=CardType.DRAGON
    zone(p,'bench',[dragon])
    assert recipients(c,r,p)==[dragon]


def test_return_enemy_basic_energy_refreshes_cached_units():
    r={'kind':'field_operation','operation':'return_enemy_energy','count':1}
    s,p,o,c,t,a=field_fixture(r)
    energy=registry.get('SVE-005')()
    t.attachment=[energy];t.energy=[CardType.PSYCHIC]
    t.dynamic_energy=False
    from packages.rules.advanced_attacks import field as resolve
    drive(resolve(r,a,s))
    assert t.energy==[] and t.attachment==[] and energy in o.left


def test_zapdos_keeps_active_damage_and_only_hits_damaged_bench():
    r=rule('11508');s,p,o,c,t,a=field_fixture(r)
    healthy=registry.get('MEW-151')();damaged=registry.get('MEW-151')()
    damaged.hp-=10
    zone(o,'bench',[healthy,damaged]);t.hp=1000;a.attack.damage=120
    c.spec['attacks'][0]['damage']=120
    before=damaged.hp;healthy_before=healthy.hp
    drive(c.reduce_action(a,s))
    assert t.hp==880 and damaged.hp==before-90 and healthy.hp==healthy_before


def test_weezing_history_reference_uses_actual_compiled_attack_name():
    from packages.rules.plain import SPECS
    spec=next(s for s in SPECS if s['effectKey']=='P4P-CHS947029AB1D606594')
    first,second=spec['attacks']
    r=second['mechanic']
    assert r['name']==first['name']
    s,p,o,c,t,a=field_fixture(r)
    c.attack_history=[{'turn':s.turn_number-2,'name':first['name']}]
    from packages.rules.attack_math import damage
    a.attack.damage=second['damage']
    assert damage(r,a,s)==second['damage']+120
    c.attack_history[0]['turn']-=2
    assert damage(r,a,s)==second['damage']

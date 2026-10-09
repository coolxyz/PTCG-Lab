from ptcg.core.enums import CardType, Stage
from ptcg.core.ability import PassiveAbility
from ptcg.core.enums import AbilityType
from ptcg.core.reducer import _calculate_damage
from ptcg.utils.utils import switch_pokemon
from packages.rules.suppression import enabled
from packages.rules.pokemon_types import has_type
from packages.rules.maximum_hp import reconcile
from tests.cardpool.test_checkup import board
from test_effects import zone


def ability(c, name, r):
    c.ability = [PassiveAbility({"name":name,"text":"fixture","abilityType":AbilityType.PASSIVE_ABILITY})]
    c.spec={"abilities":[r]}


def klefki(c):
    c.stage=Stage.BASIC
    ability(c,"Mischievous Lock",{"kind":"suppress_abilities","targets":"basic","activeOnly":True,"exceptAbility":"Mischievous Lock"})


def flutter(c):
    c.stage=Stage.BASIC
    ability(c,"Midnight Fluttering",{"kind":"suppress_abilities","targets":"opponent_active","activeOnly":True,"exceptAbility":"Midnight Fluttering"})


def test_dual_type_weakness_and_resistance_both_apply():
    s,p,o=board();c=p.active[0];t=o.active[0]
    c.cardType=CardType.GRASS
    ability(c,"Double Type",{"kind":"dual_type","types":["GRASS","FIRE"]})
    t.weakness=[CardType.FIRE];t.resistance=[CardType.GRASS]
    reconcile(s)
    assert has_type(c,"FIRE") and has_type(c,"GRASS")
    assert _calculate_damage(c,t,100,s)==170
    c.ability_blocked_turn=s.turn_number
    assert not has_type(c,"FIRE") and _calculate_damage(c,t,100,s)==70


def test_mutual_lock_setup_prioritizes_starter_and_switch_preserves_existing():
    s,p,o=board();s.starting_player=p.id
    k=p.active[0];f=o.active[0];klefki(k);flutter(f)
    assert enabled(k,s) and not enabled(f,s)
    switch_pokemon(k,p.bench[0],p)
    assert enabled(f,s)
    switch_pokemon(p.active[0],k,p)
    assert enabled(f,s) and not enabled(k,s)


def test_benched_klefki_does_not_claim_priority_before_entering_active():
    s,p,o=board();s.starting_player=p.id
    k=p.bench[0];f=o.active[0];klefki(k);flutter(f)
    assert enabled(k,s) and enabled(f,s)
    switch_pokemon(p.active[0],k,p)
    assert enabled(f,s) and not enabled(k,s)


def test_two_klefki_do_not_suppress_each_other():
    s,p,o=board();klefki(p.active[0]);klefki(o.active[0])
    assert enabled(p.active[0],s) and enabled(o.active[0],s)
    assert not enabled(p.bench[0],s)

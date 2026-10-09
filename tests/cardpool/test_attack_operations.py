from ptcg.core.card_registry import registry
from ptcg.core.enums import SpecialCondition, Stage
from packages.rules.attack_operations import resolve
from tests.cardpool.test_field_effects import field_fixture
from test_effects import drive, zone


def test_counter_allocation_is_effect_protected_and_checks_ko_after_all_counters():
    r = {"kind":"field_operation","operation":"allocate_counters","count":3}
    s,p,o,c,t,a = field_fixture(r)
    zone(o,"bench",[])
    t.hp=10
    prompts=drive(resolve(r,a,s))
    assert t.hp==-20 and len(prompts)==3 and t in o.active
    t.hp=10
    t.attack_protection={"turn":s.turn_number,"effects":True}
    drive(resolve(r,a,s))
    assert t.hp==10


def test_active_only_counters_do_not_hit_the_bench():
    from packages.rules.field_effects import resolve as field
    r={"kind":"place_counters","zone":"active","amount":50}
    s,p,o,c,t,a=field_fixture(r)
    zone(o,"bench",[registry.get("P01-005")()])
    before=t.hp;bench=o.bench[0].hp
    drive(field(r,a,s))
    assert t.hp==before-50 and o.bench[0].hp==bench


def test_recovery_can_bench_an_evolution_without_an_evolution_stack():
    r={"kind":"field_operation","operation":"recover_bench","count":3,"type":"WATER","optional":True}
    s,p,o,c,t,a=field_fixture(r)
    from ptcg.core.enums import CardType
    candidate=registry.get("P01-005")()
    candidate.stage,candidate.cardType=Stage.STAGE_2,CardType.WATER
    zone(p,"discard",[candidate])
    drive(resolve(r,a,s))
    assert candidate in p.bench and candidate.stage==Stage.STAGE_2 and not candidate.evolved


def test_gust_status_targets_the_new_active_and_respects_its_immunity():
    r={"kind":"field_operation","operation":"gust_status","status":"ASLEEP"}
    s,p,o,c,t,a=field_fixture(r)
    new=registry.get("P01-005")()
    new.attack_protection={"turn":s.turn_number,"effects":True}
    zone(o,"bench",[new])
    drive(resolve(r,a,s))
    assert o.active==[new] and t in o.bench
    assert getattr(new,"special_condition",SpecialCondition.NONE)==SpecialCondition.NONE


def test_returning_multienergy_moves_the_physical_card_and_refreshes_units():
    r={"kind":"field_operation","operation":"return_opponent_energy"}
    s,p,o,c,t,a=field_fixture(r)
    energy=registry.get("P01-004")()
    t.attachment=[energy]
    drive(resolve(r,a,s))
    assert energy in o.hand and not t.attachment and not t.energy


def test_status_choice_has_explicit_labels_and_only_applies_selected_condition():
    r={"kind":"field_operation","operation":"chosen_status","statuses":["BURNED","CONFUSED","POISONED"]}
    s,p,o,c,t,a=field_fixture(r)
    gen=resolve(r,a,s)
    info=next(gen)[3]
    actions=info["raw_available_actions"]
    assert [x.to_dict()["label"] for x in actions]==["灼伤","混乱","中毒"]
    try:gen.send(actions[1])
    except StopIteration:pass
    assert t.special_condition==SpecialCondition.CONFUSED and not getattr(t,"burned",False) and not getattr(t,"poisoned",False)

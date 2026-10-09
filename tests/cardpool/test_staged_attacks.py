from ptcg.core.card_registry import registry
from ptcg.core.enums import Stage, CardPosition
from packages.rules.staged_attacks import resolve, pay
from tests.cardpool.test_field_effects import field_fixture
from test_effects import drive, zone


def test_required_six_card_payment_is_all_or_nothing():
    r = {"kind": "staged_attack", "required": True, "cost": {"kind": "hand", "count": 6, "filter": "basic_energy"}, "effects": [{"kind": "field_operation", "operation": "knockout", "zone": "active"}]}
    s, p, o, c, t, a = field_fixture(r)
    cards = [registry.get("SVE-008")() for _ in range(5)]
    zone(p, "hand", cards)
    before = t.hp
    drive(resolve(r, a, s))
    assert t.hp == before and all(x in p.hand for x in cards)


def test_failed_bonus_condition_does_not_pay_or_resolve_extra_effect():
    r = {"kind": "staged_attack", "condition": {"term": "opponent_evolved"}, "bonus": 140, "effects": [{"kind": "discard_self_energy", "count": "all"}]}
    s, p, o, c, t, a = field_fixture(r)
    t.stage = Stage.BASIC
    t.hp = 1000
    a.attack.damage = 140
    energy = registry.get("SVE-008")()
    c.attachment = [energy]
    drive(resolve(r, a, s))
    assert t.hp == 860 and energy in c.attachment


def test_required_hand_payment_failure_ends_turn_without_damage():
    r={"kind":"staged_attack","required":True,"cost":{"kind":"hand"}}
    s,p,o,c,t,a=field_fixture(r)
    zone(p,"hand",[])
    before=t.hp
    drive(resolve(r,a,s))
    assert t.hp==before and s.turn==o.id


def test_optional_two_units_can_use_one_double_energy_but_cannot_pay_one_unit():
    r={"kind":"staged_attack","optional":True,"cost":{"kind":"energy","count":2},"bonus":100}
    s,p,o,c,t,a=field_fixture(r)
    energy=registry.get("SVE-008")()
    c.attachment=[energy];c.dynamic_energy=True
    assert not drive(pay(r,a,s)) and c.attachment==[energy]
    from ptcg.cards.BRS.double_turbo_energy import BRS151DoubleTurboEnergy
    double=BRS151DoubleTurboEnergy()
    c.attachment=[double]
    drive(pay(r,a,s))
    assert double in p.discard and not c.attachment


def test_optional_recoil_is_resolved_after_bonus_damage():
    r={"kind":"staged_attack","optional":True,"bonus":30,"effects":[{"kind":"recoil","amount":20}]}
    s,p,o,c,t,a=field_fixture(r)
    a.attack.damage=50;t.hp=1000
    before=c.hp
    drive(resolve(r,a,s))
    assert t.hp==920 and c.hp==before-20


def test_mill_damage_uses_discarded_window_and_printed_retreat_cost():
    r={"kind":"staged_attack","top":5,"mode":"multiply","factor":80,"retreat":4}
    s,p,o,c,t,a=field_fixture(r)
    candidates=[registry.get("SVE-008")() for _ in range(3)]
    from packages.rules.plain import SPECS
    high=registry.get(next(x["effectKey"] for x in SPECS if x["retreat"]==4))()
    high.retreat=[]
    candidates.append(high)
    zone(p,"left",candidates)
    t.hp=1000
    drive(resolve(r,a,s))
    assert t.hp==920 and all(x in p.discard for x in candidates)


def test_energy_return_cost_cleans_attachment_and_deck_indices():
    r={"kind":"staged_attack","cost":{"kind":"energy","count":3,"destination":"left"}}
    s,p,o,c,t,a=field_fixture(r)
    cards=[registry.get("SVE-008")() for _ in range(3)]
    c.attachment=cards[:];c.dynamic_energy=True
    drive(pay(r,a,s))
    assert not c.attachment and not c.energy and all(x in p.left for x in cards)
    assert all(x.index==i+1 and x.cardPosition==CardPosition.LEFT for i,x in enumerate(p.left))

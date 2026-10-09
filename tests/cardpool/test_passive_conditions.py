from ptcg.core.card_registry import registry
from ptcg.core.action import UseItemAction, PutStadiumAction
from ptcg.core.enums import CardType
from packages.rules.hand_locks import allowed
from packages.rules.modifiers import refresh_costs
from packages.rules.core_fixes import shield_damage
from packages.rules.maximum_hp import reconcile, maximum
from packages.rules.activated_effects import available
from tests.cardpool.test_checkup import board
from test_effects import zone


def set_rules(c, rules):
    c.spec = {**getattr(c,"spec",{}),"abilities":rules}


def test_active_item_lock_leaves_stadium_and_bench_holder_unrestricted():
    s,p,o=board()
    holder=o.active[0]
    set_rules(holder,[{"kind":"hand_lock","activeOnly":True,"cards":"item"}])
    item=UseItemAction(p.id,registry.get("P01-001")())
    stadium=PutStadiumAction(p.id,registry.get("P01-001")())
    assert not allowed(item,p,s) and allowed(stadium,p,s)
    zone(o,"active",[o.bench[0]])
    zone(o,"bench",[holder])
    assert allowed(item,p,s)


def test_equal_hand_cost_is_named_and_reverts_when_condition_changes():
    s,p,o=board()
    c=p.active[0]
    original=list(type(c)().attacks[0].cost)
    set_rules(c,[{"kind":"continuous","scope":"self","equalHands":True,"attackName":c.attacks[0].name,"attackCost":[]}])
    zone(p,"hand",[]);zone(o,"hand",[])
    refresh_costs(c,s)
    assert c.attacks[0].cost == []
    zone(o,"hand",[registry.get("SVE-008")()])
    refresh_costs(c,s)
    assert c.attacks[0].cost == original


def test_energy_threshold_hp_preserves_damage_and_reverses_when_energy_leaves():
    s,p,o=board()
    c=p.active[0]
    base=maximum(c)
    set_rules(c,[{"kind":"continuous","scope":"self","typedEnergyMinimum":{"type":"GRASS","count":6},"hp":250}])
    c.hp-=20
    c.attachment=[registry.get("P4E-001")() for _ in range(6)]
    c.dynamic_energy=True
    reconcile(s)
    assert maximum(c)==base+250 and c.hp==base+230
    c.attachment.pop()
    reconcile(s)
    assert maximum(c)==base and c.hp==base-20


def test_large_damage_prevention_applies_after_damage_reductions():
    s,p,o=board()
    target=o.active[0]
    set_rules(target,[{"kind":"continuous","scope":"self","preventDamageAtLeast":200,"armor":30}])
    assert shield_damage(target,200,s,p.active[0])==170
    assert shield_damage(target,230,s,p.active[0])==0


def test_named_supporter_condition_uses_actual_played_action():
    s,p,o=board()
    rule={"requiresSupporter":"Janine's Secret Art","effect":{"kind":"draw_until","count":8}}
    assert not available(p.active[0],rule,s)
    p.current_turn_actions=[{"action_type":"UseSupporterAction","source":"Janine's Secret Art"}]
    assert available(p.active[0],rule,s)

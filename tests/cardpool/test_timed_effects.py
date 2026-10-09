import pytest
from ptcg.core.enums import Coin, SpecialCondition
from ptcg.core.action import AttackAction
from ptcg.utils.utils import switch_pokemon
from packages.rules.attack_operations import resolve
from packages.rules.modifiers import refresh_costs, attack_damage
from packages.rules.core_fixes import condition_checkup
from tests.cardpool.test_field_effects import field_fixture
from test_effects import drive, zone


def test_timed_cost_increase_is_one_turn_and_clears_on_switch():
    r={"kind":"field_operation","operation":"timed_modifier","key":"cost","modifier":{"attackMore":2,"retreatMore":1}}
    s,p,o,c,t,a=field_fixture(r)
    costs=list(t.attacks[0].cost);retreat=list(t.retreat)
    drive(resolve(r,a,s))
    refresh_costs(t,s)
    assert t.attacks[0].cost==costs
    s.turn_number+=1
    refresh_costs(t,s)
    assert len(t.attacks[0].cost)==len(costs)+2 and len(t.retreat)==len(retreat)+1
    from ptcg.core.card_registry import registry
    zone(o,"bench",[registry.get("P01-005")()])
    switch_pokemon(t,o.bench[0],o)
    refresh_costs(t,s)
    assert t.attacks[0].cost==costs and t.retreat==retreat


def test_named_next_turn_bonus_only_applies_to_the_named_attack_and_does_not_stack():
    r={"kind":"field_operation","operation":"timed_modifier","key":"Excited Punch","self":True,"nextOwnTurn":True,"modifier":{"damage":60,"attackName":"Excited Punch"}}
    s,p,o,c,t,a=field_fixture(r)
    for _ in range(2):drive(resolve(r,a,s))
    c.resolving_attack_name="Excited Punch"
    assert attack_damage(c,t,20,s)==20
    s.turn_number+=2
    assert attack_damage(c,t,20,s)==80
    c.resolving_attack_name="Other"
    assert attack_damage(c,t,20,s)==20


@pytest.mark.parametrize("results,recover",[([Coin.HEAD,Coin.HEAD],True),([Coin.HEAD,Coin.TAIL],False),([Coin.TAIL,Coin.HEAD],False)])
def test_double_sleep_always_flips_both_coins_and_requires_two_heads(results,recover,monkeypatch):
    s,p,o,c,t,a=field_fixture({"kind":"recover_status"})
    c.special_condition=SpecialCondition.ASLEEP
    c.sleep_coins=2
    calls=[]
    def flip(state, *, during_turn=True):
        assert during_turn is False
        calls.append(1)
        return results[len(calls)-1]
    monkeypatch.setattr("ptcg.utils.utils.flip_coin",flip)
    condition_checkup(s)
    assert len(calls)==2
    assert (getattr(c,"special_condition",SpecialCondition.NONE)==SpecialCondition.NONE)==recover


def test_new_confusion_replaces_old_confusion_counter_amount():
    from packages.rules.status_immunity import apply_status
    s,p,o,c,t,a=field_fixture({"kind":"special_status","status":"CONFUSED","target":"opponent","coin":False,"confusionDamage":80})
    drive(c.resolve_mechanic(c.spec["attacks"][0]["mechanic"],a,s))
    assert t.confusion_damage==80
    apply_status(t,SpecialCondition.CONFUSED)
    assert getattr(t,"confusion_damage",30)==30


def test_smokescreen_coin_failure_ends_turn_without_attack_damage(monkeypatch):
    from packages.rules.engine import RulesEngine
    # Use a fresh fixture engine to exercise the action gate directly.
    s,p,o,c,t,a=field_fixture({"kind":"recover_status"})
    e=object.__new__(RulesEngine)
    e.gamestate=s
    c.attack_coin_check={"turn":s.turn_number,"count":2}
    monkeypatch.setattr("ptcg.utils.utils.flip_coin",lambda state, *args, **kwargs:Coin.TAIL)
    gen=e._reduce_action();next(gen)
    before=t.hp
    try:gen.send(a)
    except StopIteration:pass
    assert t.hp==before and s.turn!=p.id

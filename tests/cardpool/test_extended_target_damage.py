from ptcg.core.card_registry import registry
from ptcg.core.enums import PokemonType, CardType
from packages.rules.target_damage import resolve
from packages.rules.staged_attacks import resolve as staged
from packages.rules.core_fixes import refresh_energy
from tests.cardpool.test_field_effects import field_fixture
from test_effects import drive, zone


def test_target_counter_damage_uses_selected_target_not_original_active():
    rule={"kind":"target_damage","amount":20,"count":1,"zone":"bench","targetCounters":True}
    s,p,o,c,t,a=field_fixture(rule)
    bench=registry.get("P01-006")()
    zone(o,"bench",[bench])
    before=bench.hp
    bench.hp-=20
    drive(resolve(rule,a,s))
    assert bench.hp==before-60


def test_ex_only_spread_ignores_weakness_but_still_respects_damage_reduction():
    rule={"kind":"target_damage","amount":60,"count":60,"zone":"all","ex":True,"ignoreWeaknessResistance":True}
    s,p,o,c,t,a=field_fixture(rule)
    t.pokemonType=PokemonType.EX;t.hp=300;t.weakness=[c.cardType]
    t.spec={"abilities":[{"kind":"continuous","scope":"self","armor":20}]}
    ordinary=registry.get("P01-005")();zone(o,"bench",[ordinary]);hp=ordinary.hp
    drive(resolve(rule,a,s))
    assert t.hp==260 and ordinary.hp==hp


def test_typed_all_discard_preserves_other_energy():
    rule={"kind":"target_damage","amount":10,"count":1,"zone":"all","discardEnergy":"all","discardType":"FIRE"}
    s,p,o,c,t,a=field_fixture(rule)
    fire,metal=registry.get("SVE-002")(),registry.get("SVE-008")()
    c.attachment=[fire,metal];c.dynamic_energy=True;refresh_energy(c)
    drive(resolve(rule,a,s))
    assert fire in p.discard and metal in c.attachment


def test_self_counters_can_exceed_remaining_hp_and_damage_resolves_before_ko():
    rule={"kind":"staged_attack","mode":"multiply","factor":20,"selfCounters":9}
    s,p,o,c,t,a=field_fixture(rule)
    c.hp=10;t.hp=300;t.weakness=[];t.resistance=[]
    zone(p,"bench",[registry.get("P01-005")()])
    drive(staged(rule,a,s))
    assert t.hp==120 and c in p.discard and len(o.prize)==6-c.prize


def test_ignore_only_weakness_still_applies_resistance():
    from ptcg.core.reducer import reduce_attack_damage
    s,p,o,c,t,a=field_fixture({"kind":"draw","count":1})
    t.hp=300;t.weakness=[c.cardType];t.resistance=[c.cardType]
    a.attack.damage=100
    drive(reduce_attack_damage(a,s,ignore_weakness=True))
    assert t.hp==230

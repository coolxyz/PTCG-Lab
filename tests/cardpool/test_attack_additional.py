from ptcg.core.card_registry import registry
from packages.rules.field_effects import resolve as field
from packages.rules.zone_effects import resolve as zone_effect
from packages.rules.maximum_hp import maximum
from tests.cardpool.test_field_effects import field_fixture
from test_effects import drive, zone


def test_healing_selects_only_damaged_bench_and_full_heal_uses_maximum_hp():
    r = {"kind":"heal_field", "zone":"bench", "target":"one", "full":True}
    s, p, o, c, t, a = field_fixture(r)
    c.hp -= 10
    active_hp = c.hp
    zone(p, "bench", [registry.get("P01-005")()])
    benched = p.bench[0]
    benched.hp -= 30
    drive(field(r, a, s))
    assert benched.hp == maximum(benched) and c.hp == active_hp


def test_multi_bench_damage_chooses_distinct_targets_and_records_actual_damage():
    r = {"kind":"bench_damage", "amount":20, "count":2}
    s, p, o, c, t, a = field_fixture(r)
    zone(o, "bench", [registry.get("P01-005")() for _ in range(3)])
    before = {id(x):x.hp for x in o.bench}
    drive(field(r, a, s))
    assert sorted(before[id(x)] - x.hp for x in o.bench) == [0,20,20]
    assert len(a.group_damage_targets) == 3


def test_own_bench_damage_hits_only_selected_pokemon():
    r = {"kind":"own_bench_damage", "amount":20, "select":True}
    s, p, o, c, t, a = field_fixture(r)
    zone(p, "bench", [registry.get("P01-005")() for _ in range(2)])
    before = [x.hp for x in p.bench]
    drive(field(r, a, s))
    assert sorted(h-x.hp for h,x in zip(before,p.bench)) == [0,20]


def test_named_evolution_search_can_place_stage_one_directly_without_admitting_other_cards():
    r = {"kind":"search_bench", "names":["Charjabug"], "anyStage":True, "count":3}
    s, p, o, c, t, a = field_fixture(r)
    from ptcg.core.enums import Stage
    selected = registry.get("P01-005")()
    selected.name, selected.stage = "Charjabug", Stage.STAGE_1
    other = registry.get("P01-005")()
    zone(p, "left", [selected,other])
    drive(zone_effect(r, a, s))
    assert selected in p.bench and other in p.left


def test_two_random_cards_are_revealed_and_shuffled_back_together():
    r = {"kind":"random_discard_hand", "count":2, "destination":"deck"}
    s, p, o, c, t, a = field_fixture(r)
    cards = [registry.get("SVE-008")() for _ in range(3)]
    zone(o,"hand",cards[:])
    before = len(o.left)
    drive(zone_effect(r,a,s))
    assert len(o.hand)==1 and len(o.left)==before+2 and len(s.public_reveals[-1]["cards"])==2


def test_draw_to_opponent_hand_never_discards_when_already_ahead():
    r = {"kind":"draw_until", "opponentHand":True}
    s, p, o, c, t, a = field_fixture(r)
    zone(p,"hand",[])
    zone(o,"hand",[registry.get("SVE-008")() for _ in range(3)])
    drive(zone_effect(r,a,s))
    assert len(p.hand)==3
    zone(o,"hand",[])
    drive(zone_effect(r,a,s))
    assert len(p.hand)==3

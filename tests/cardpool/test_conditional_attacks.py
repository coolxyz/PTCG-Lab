import pytest
from tests.cardpool.test_field_effects import field_fixture
from test_effects import drive


@pytest.mark.parametrize("prizes,succeeds", [(2, False), (3, True), (4, True), (5, False)])
def test_attack_failure_is_legal_but_ends_turn_without_damage(prizes, succeeds):
    s, p, o, source, target, action = field_fixture({"kind": "conditional_attack", "condition": {"term": "opponent_prizes", "in": [3, 4]}})
    o.prize = o.prize[:prizes]
    action.attack.damage = 120
    target.hp = 1000
    before = target.hp
    drive(source.reduce_action(action, s))
    assert target.hp == before - (120 if succeeds else 0)
    assert s.turn == o.id


@pytest.mark.parametrize("counters,succeeds", [(0, True), (3, True), (4, False), (5, False)])
def test_counter_threshold_reads_damage_counters_before_attack(counters, succeeds):
    s, p, o, source, target, action = field_fixture({"kind": "conditional_attack", "condition": {"term": "self_counters", "lessThan": 4}})
    source.hp -= counters * 10
    action.attack.damage = 20
    before = target.hp
    drive(source.reduce_action(action, s))
    assert target.hp == before - (20 if succeeds else 0)

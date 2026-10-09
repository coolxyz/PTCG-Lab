import random
import pytest
from ptcg.core.enums import SpecialCondition, Coin
from ptcg.utils.utils import flip_coin
from ptcg.core.card_registry import registry
from tests.cardpool.test_field_effects import field_fixture
from test_effects import drive, zone


@pytest.mark.parametrize("heads", [True, False])
def test_one_coin_controls_bonus_and_healing_without_second_flip(heads):
    rule = {"kind":"coin_branch", "flips":1, "bonus":[0,30], "heads":[{"kind":"heal_self", "amount":30}]}
    s, p, o, c, t, a = field_fixture(rule)
    c.hp -= 40
    previous = c.hp
    t.hp, a.attack.damage = 1000, 30
    s.rng = random.Random(1 if heads else 0)
    expected = random.Random(1 if heads else 0)
    expected.randint(0, 1)
    drive(c.reduce_action(a, s))
    assert t.hp == 1000 - (60 if heads else 30)
    assert c.hp == previous + (30 if heads else 0)
    assert s.rng.getstate() == expected.getstate()


@pytest.mark.parametrize("seed", range(5))
def test_coin_bonus_table_uses_total_heads_and_keeps_printed_damage(seed):
    r = {"kind":"coin_branch", "flips":3, "bonus":[0,20,60,120]}
    s, p, o, c, t, a = field_fixture(r)
    s.rng = random.Random(seed)
    heads = sum(flip_coin(s) == Coin.HEAD for _ in range(3))
    s.rng = random.Random(seed)
    t.hp, a.attack.damage = 1000, 50
    drive(c.reduce_action(a, s))
    assert t.hp == 950 - r["bonus"][heads]
    assert c.attacks[0].damage == 0


def test_zero_heads_still_applies_confusion_without_damage():
    r = {"kind":"coin_branch", "flips":2, "perHead":90, "tails":[{"kind":"special_status", "status":"CONFUSED", "target":"opponent", "coin":False}]}
    s, p, o, c, t, a = field_fixture(r)
    s.rng = random.Random(0)
    before = t.hp
    drive(c.reduce_action(a, s))
    assert t.hp == before and t.special_condition == SpecialCondition.CONFUSED


def test_fly_tails_fails_and_does_not_grant_protection():
    r = {"kind":"coin_branch", "flips":1, "failZero":True, "heads":[{"kind":"attack_protection", "damage":True, "effects":True}]}
    s, p, o, c, t, a = field_fixture(r)
    s.rng = random.Random(0)
    before, a.attack.damage = t.hp, 60
    drive(c.reduce_action(a, s))
    assert t.hp == before and not hasattr(c, "attack_protection") and s.turn == o.id


def test_per_head_mill_caps_at_available_deck_and_preserves_rng():
    r = {"kind":"coin_branch", "flips":3, "perHeadEffect":{"kind":"mill_opponent", "count":3}}
    s, p, o, c, t, a = field_fixture(r)
    zone(o, "left", [registry.get("SVE-008")() for _ in range(10)])
    s.rng = random.Random(1)
    heads = sum(flip_coin(s) == Coin.HEAD for _ in range(3))
    s.rng = random.Random(1)
    drive(c.reduce_action(a, s))
    assert len(o.discard) >= heads * 3
    assert len(o.left) == 10 - heads * 3 - 1


def test_up_to_recovery_can_choose_zero_and_all_heads_instant_ko_is_not_damage():
    from packages.rules.coin_branches import resolve
    r = {"kind":"coin_branch", "flips":3, "perHeadEffect":{"kind":"recover_hand", "filter":"any", "count":1, "optional":True}}
    s, p, o, c, t, a = field_fixture(r)
    original = registry.get("SVE-008")()
    zone(p, "discard", [original])
    s.rng = random.Random(1)
    drive(resolve(r, a, s), lambda actions, info, n: actions[0])
    assert original in p.discard

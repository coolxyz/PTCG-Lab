import random

from ptcg.core.card_registry import registry
from ptcg.core.enums import CardPosition, SpecialCondition
from packages.rules.activated_effects import available, resolve
from packages.rules.entry_effects import effect
from packages.rules.modifiers import attack_damage
from tests.cardpool.test_checkup import board
from test_effects import drive, zone


def test_attached_basic_cost_is_paid_before_draw_and_does_not_accept_special():
    s, p, o = board()
    c = p.active[0]
    r = {"cost":{"origin":"attached", "energy":True, "basic":True, "type":"LIGHTNING"}, "effect":{"kind":"draw_until", "count":6}}
    zone(p, "hand", [])
    c.attachment = [registry.get("SVE-002")()]
    assert not available(c, r, s)
    lightning = registry.get("SVE-004")()
    c.attachment = [lightning]
    assert available(c, r, s)
    drive(resolve(c, r, s))
    assert lightning in p.discard and not c.attachment and len(p.hand) == 6


def test_counter_transfer_can_knock_out_recipient_and_does_not_heal_by_effect():
    s, p, o = board()
    c, donor = p.active[0], p.bench[0]
    donor.hp -= 20
    before = donor.hp
    c.hp = 10
    drive(effect(c, {"kind":"move_counter", "target":"self"}, s))
    assert donor.hp == before + 10 and c.hp == 0


def test_fire_transfer_moves_one_physical_energy_and_reindexes_both_pokemon():
    s, p, o = board()
    donor = p.bench[0]
    energy = registry.get("SVE-002")()
    donor.attachment = [energy]
    donor.dynamic_energy = True
    r = {"effect":{"kind":"move_energy", "type":"FIRE"}}
    assert available(donor, r, s)
    drive(resolve(donor, r, s))
    assert not donor.attachment and energy in p.active[0].attachment
    assert energy.index == 1 and energy.cardPosition == CardPosition.ACTIVE_ATTACHMENT
    assert not available(donor, r, s)


def test_read_stars_moves_only_unselected_card_and_preserves_hidden_information():
    s, p, o = board()
    cards = [registry.get("SVE-002")(), registry.get("SVE-004")(), registry.get("SVE-008")()]
    zone(o, "left", cards[:])
    public = list(s.public_reveals)
    prompts = drive(effect(p.active[0], {"kind":"order_opponent_top"}, s))
    assert o.left == [cards[1], cards[2], cards[0]]
    assert [c.index for c in o.left] == [1, 2, 3]
    assert s.public_reveals == public and len(prompts) == 1


def test_coin_gust_confuses_new_active_only_and_no_bench_means_no_status():
    s, p, o = board()
    old, new = o.active[0], o.bench[0]
    r = {"kind":"coin_effect", "heads":{"kind":"gust", "status":"CONFUSED"}}
    s.rng = random.Random(1)
    drive(effect(p.active[0], r, s))
    assert o.active == [new] and new.special_condition == SpecialCondition.CONFUSED
    assert getattr(old, "special_condition", SpecialCondition.NONE) == SpecialCondition.NONE
    zone(o, "bench", [])
    new.special_condition = SpecialCondition.NONE
    s.rng = random.Random(1)
    drive(effect(p.active[0], r, s))
    assert new.special_condition == SpecialCondition.NONE


def test_team_bonus_stacks_for_turn_and_applies_to_later_arriving_pokemon():
    s, p, o = board()
    for _ in range(2):
        drive(effect(p.active[0], {"kind":"team_damage", "amount":60}, s))
    c = registry.get("P01-005")()
    zone(p, "active", [c])
    assert attack_damage(c, o.active[0], 10, s) == 130
    assert attack_damage(c, o.bench[0], 10, s) == 10
    s.turn_number += 2
    assert attack_damage(c, o.active[0], 10, s) == 10

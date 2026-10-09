from ptcg.core.card_registry import registry
from ptcg.utils.utils import next_turn
from packages.rules.end_phase import finish_pending
from packages.rules.knockouts import resolve_group
from tests.cardpool.test_checkup import board
from test_effects import zone, drive
import random


def rule(card, r):
    card.spec = {**getattr(card, "spec", {}), "abilities": [r]}


def test_end_draw_is_optional_and_precedes_next_player_draw():
    s, p, o = board()
    zone(p, "hand", [])
    rule(p.bench[0], {"kind": "end_turn", "optional": True, "drawUntil": 8})
    next_turn(s)
    assert s.turn == p.id and len(o.left) == 8
    drive(finish_pending(s))
    assert len(p.hand) == 8 and len(o.left) == 7 and s.turn == o.id


def test_mandatory_end_mill_once_and_bench_inactive():
    s, p, o = board()
    rule(p.active[0], {"kind": "end_turn", "activeOnly": True, "mill": 5})
    rule(p.bench[0], {"kind": "end_turn", "activeOnly": True, "mill": 5})
    next_turn(s)
    drive(finish_pending(s))
    assert len(p.left) == 3 and len(p.discard) == 5 and s.turn == o.id


def test_delayed_discard_evaluates_end_hand_and_orders_with_draw():
    s, p, o = board()
    zone(p, "hand", [])
    rule(p.active[0], {"kind": "end_turn", "optional": True, "drawUntil": 8})
    p.end_turn_effects = [{"turn": s.turn_number, "discardHandAtLeast": 5}]
    next_turn(s)
    gen = finish_pending(s)
    item = next(gen)
    item = gen.send(item[3]["raw_available_actions"][0])
    try:
        while True:
            item = gen.send(list(item[3]["raw_available_actions"])[-1])
    except StopIteration:
        pass
    assert len(p.hand) == 0 and len(p.discard) == 8


def test_coin_prize_prevention_applies_to_nonattack_bench_knockout():
    s, p, o = board()
    s.rng = random.Random(1)
    t = o.bench[0]
    rule(t, {"kind": "knockout_prizes", "coin": True, "none": True})
    t.hp = 0
    drive(resolve_group(s, checkup=True))
    assert len(p.prize) == 6 and t in o.discard


def test_wonder_kiss_does_not_stack_and_applies_to_checkup():
    s, p, o = board()
    s.rng = random.Random(1)
    for c in p.active+p.bench:
        rule(c, {"kind": "knockout_prizes", "coin": True, "bonus": 1, "noStack": "Wonder Kiss"})
    o.active[0].hp = 0
    drive(resolve_group(s, checkup=True))
    assert len(p.prize) == 4

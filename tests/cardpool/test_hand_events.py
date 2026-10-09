from ptcg.core.card_registry import registry
from ptcg.core.enums import CardPosition, Stage
from ptcg.utils.utils import move_cards
from packages.rules.hand_events import finish, bench_actions
from tests.cardpool.test_checkup import board
from tests.cardpool.test_dual_and_locks import ability
from test_effects import zone, drive


def test_hand_energy_triggers_stack_but_named_pulse_does_not():
    s, p, o = board()
    target = p.active[0]
    for c in o.active + o.bench:
        ability(c, "Buddy Pulse", {"kind": "hand_event", "event": "energy", "opponent": True,
                                  "counters": 20, "noStack": "Buddy Pulse"})
    energy = registry.get("SVE-008")()
    zone(p, "hand", [energy])
    before = target.hp
    move_cards(energy, (p.id, CardPosition.HAND), (p.id, CardPosition.ACTIVE_ATTACHMENT, 1), s)
    drive(finish(s))
    assert target.hp == before - 20
    for c in o.active + o.bench:
        del c.spec["abilities"][0]["noStack"]
    energy = registry.get("SVE-008")()
    zone(p, "hand", [energy])
    move_cards(energy, (p.id, CardPosition.HAND), (p.id, CardPosition.ACTIVE_ATTACHMENT, 1), s)
    drive(finish(s))
    assert target.hp == before - 60


def test_deck_attachment_does_not_trigger_and_suppressed_ability_does_not():
    s, p, o = board()
    ability(o.active[0], "Gnawing Curse", {"kind": "hand_event", "event": "energy", "opponent": True, "counters": 20})
    move_cards(p.left[0], (p.id, CardPosition.LEFT), (p.id, CardPosition.ACTIVE_ATTACHMENT, 1), s)
    assert not getattr(s, "hand_event_queue", [])
    o.active[0].ability_blocked_turn = s.turn_number
    energy = registry.get("SVE-008")()
    zone(p, "hand", [energy])
    move_cards(energy, (p.id, CardPosition.HAND), (p.id, CardPosition.ACTIVE_ATTACHMENT, 1), s)
    assert not getattr(s, "hand_event_queue", [])


def test_hand_bench_ability_requires_condition_and_space():
    s, p, o = board()
    c = registry.get("P01-005")()
    ability(c, "Emergency Rotation", {"kind": "hand_bench", "opponentStage2": True})
    zone(p, "hand", [c])
    o.active[0].stage = Stage.BASIC
    o.bench[0].stage = Stage.BASIC
    assert not bench_actions(p, s)
    o.active[0].stage = Stage.STAGE_2
    assert len(bench_actions(p, s)) == 1
    p.benchSize = len(p.bench)
    assert not bench_actions(p, s)

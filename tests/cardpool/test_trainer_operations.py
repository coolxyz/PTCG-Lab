import random
import pytest
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardPosition
from packages.rules.trainer_operations import resolve
from packages.rules.plain import TRAINER_SPECS
from tests.cardpool.test_shared_trainers import board, play
from test_effects import zone, drive


def test_energy_return_keeps_physical_identity_and_deck_top():
    s,p,o,c = board("trainer_operation",name="Team Star Grunt")
    energy = registry.get("SVE-008")()
    o.active[0].attachment = [energy]
    tail = list(o.left)
    play(s,c)
    assert o.left == [energy]+tail
    assert energy.cardPosition == CardPosition.LEFT and energy.index == 1
    assert not o.active[0].attachment and not o.active[0].energy


def test_ortega_draw_decision_belongs_to_opponent():
    s,p,o,c = board("trainer_operation",name="Ortega")
    old = registry.get("SVE-008")()
    zone(o,"hand",[old])
    top = o.left[0]
    seen = drive(c.reduce_action(c.get_actions(s)[0],s))
    assert seen[-1]["raw_available_actions"][0].playerId == o.id
    assert old in o.left and top in o.hand


def test_crispin_cannot_select_duplicate_types_and_one_card_goes_to_hand():
    s,p,o,c = board("trainer_operation",name="Crispin")
    energy = [registry.get("SVE-008")() for _ in range(3)]
    zone(p,"left",energy)
    before = len(p.hand)
    play(s,c)
    assert len(p.hand) == before and len(p.left) == 2
    assert not any(x.attachment for x in p.active+p.bench)


def test_jasmine_protects_new_pokemon_only_on_next_turn():
    from packages.rules.modifiers import armor
    s,p,o,c = board("trainer_operation",name="Jasmine's Gaze")
    turn = s.turn_number
    play(s,c)
    new = registry.get("P01-005")()
    zone(p,"bench",[new])
    assert armor(new,s,o.active[0]) == 0
    s.turn_number = turn+1
    assert armor(new,s,o.active[0]) == 30
    s.turn_number = turn+2
    assert armor(new,s,o.active[0]) == 0


def test_lucian_both_hands_bottom_before_drawing():
    s,p,o,c = board("trainer_operation",name="Lucian")
    left,oleft = list(p.left),list(o.left)
    hand,ohand = list(p.hand[1:]),list(o.hand)
    s.rng=random.Random(2)
    play(s,c)
    assert set(hand).issubset(p.left) and set(ohand).issubset(o.left)
    assert p.hand == left[:len(p.hand)] and o.hand == oleft[:len(o.hand)]
    assert len(p.hand) in (3,6) and len(o.hand) in (3,6)


@pytest.mark.parametrize("spec",[r for r in TRAINER_SPECS if r["mechanic"]["kind"] == "trainer_operation"],ids=lambda x:x["name"])
def test_composed_trainers_resolve_with_representative_resources(spec):
    s,p,o,c = board("trainer_operation",name=spec["name"])
    p.active[0].name = "Team Rocket's Rattata"
    zone(p,"bench",[registry.get("P01-005")()])
    zone(o,"bench",[registry.get("P01-005")()])
    p.bench[0].name = "Team Rocket's Rattata"
    p.active[0].hp -= 10
    o.active[0].attachment = [registry.get("SVE-008")()]
    zone(o,"hand",[registry.get("P01-005")()])
    zone(p,"left",[registry.get("P01-005")(),registry.get("SVE-008")(),registry.get("SVE-002")()])
    # Low-level execution covers legal empty search outcomes too.
    drive(resolve(c,spec["mechanic"],s))

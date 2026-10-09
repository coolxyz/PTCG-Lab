import pytest
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardPosition, CardType, Stage, PokemonType, SpecialCondition
from ptcg.utils.utils import move_cards
from packages.rules.plain import SPECIAL_ENERGY_SPECS
from packages.rules.maximum_hp import reconcile
from packages.rules.protection import blocked
from packages.rules.damage_events import deal, finish
from tests.cardpool.test_checkup import board


def energy(name):
    return registry.get(next(s["effectKey"] for s in SPECIAL_ENERGY_SPECS if s["name"] == name))()


def attach(s, p, target, card, origin="hand"):
    from test_effects import zone
    zone(p, origin, [card])
    pos = CardPosition.ACTIVE_ATTACHMENT if target in p.active else CardPosition.BENCH_ATTACHMENT
    move_cards(card, (p.id, CardPosition[origin.upper()]), (p.id, pos, target.index), s)
    reconcile(s)


@pytest.mark.parametrize("origin,switches", [("hand", True), ("discard", False)])
def test_jet_triggers_for_any_hand_attachment_only(origin, switches):
    s, p, o = board()
    target = p.bench[0]
    e = energy("Jet Energy")
    attach(s, p, target, e, origin)
    assert (target in p.active) == switches
    assert e in target.attachment and not p.energyPlayedTurn


def test_gift_draw_and_healing_trigger_once_without_consuming_manual_attachment():
    s, p, o = board()
    n = len(p.left)
    attach(s, p, p.active[0], energy("Enriching Energy"))
    assert len(p.left) == n - 4 and len(p.hand) == 4
    target = p.active[0]
    target.hp -= 40
    previous = target.hp
    attach(s, p, target, energy("Medical Energy"))
    assert target.hp == previous + 30
    reconcile(s)
    assert target.hp == previous + 30


def test_reversal_recomputes_supply_after_prizes_stage_and_rule_box_change():
    s, p, o = board()
    target = p.active[0]
    target.stage = Stage.STAGE_1
    o.prize.pop()
    attach(s, p, target, energy("Reversal Energy"))
    assert target.energy == [CardType.ANY] * 3
    p.prize.pop()
    reconcile(s)
    assert target.energy == [CardType.COLORLESS]
    o.prize.pop()
    target.pokemonType = PokemonType.EX
    reconcile(s)
    assert target.energy == [CardType.COLORLESS]


def test_therapy_recovers_three_conditions_and_does_not_remove_poison_or_burn():
    s, p, o = board()
    target = p.active[0]
    target.special_condition = SpecialCondition.CONFUSED
    target.poisoned = target.burned = True
    attach(s, p, target, energy("Therapeutic Energy"))
    assert not hasattr(target, "special_condition")
    assert target.poisoned and target.burned
    for condition in (SpecialCondition.PARALYZED, SpecialCondition.ASLEEP):
        target.special_condition = condition
        reconcile(s)
        assert not hasattr(target, "special_condition")


def test_mist_blocks_effects_and_counters_not_damage_or_existing_locks():
    s, p, o = board()
    target = p.active[0]
    target.attack_blocked_turn = s.turn_number
    attach(s, p, target, energy("Mist Energy"))
    assert blocked(target, s, "effects", o.active[0])
    assert blocked(target, s, "counters", o.active[0])
    assert not blocked(target, s, "damage", o.active[0])
    assert target.attack_blocked_turn == s.turn_number


def test_legacy_survival_does_not_spend_once_per_game_reduction():
    s, p, o = board()
    target = p.active[0]
    target.spec = {"abilities": [{"kind": "survive_damage", "remainingHP": 10}]}
    attach(s, p, target, energy("Legacy Energy"))
    deal(o.active[0], target, 1000, s)
    finish(s)
    assert target.hp == 10 and not getattr(p, "legacy_energy_used", False)
    deal(o.active[0], target, 1000, s)
    finish(s)
    assert target.knockout_prize_reduction == 1 and p.legacy_energy_used
    # Same owner's once-per-game flag survives moving/recovering the Energy.
    del target.knockout_prize_reduction
    target.hp = 10
    deal(o.active[0], target, 1000, s)
    finish(s)
    assert not hasattr(target, "knockout_prize_reduction")

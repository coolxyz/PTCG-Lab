from types import SimpleNamespace
import pytest
from ptcg.core.action import AttackAction, UseItemAction
from ptcg.core.card import PokemonCard, ItemCard
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType as T, CardPosition, SpecialCondition
from ptcg.core.reducer import reduce_attack_damage
from packages.rules.plain import SPECS, TRAINER_SPECS, SPECIAL_ENERGY_SPECS
from packages.rules.maximum_hp import reconcile
from packages.rules.core_fixes import check_energy, refresh_energy, end_turn_tools, discard_pokemon
from packages.rules.engine import RulesEngine
from tests.cardpool.test_checkup import board
from test_effects import drive, zone


def card(name):
    return registry.get(next(s["effectKey"] for s in SPECS + TRAINER_SPECS + SPECIAL_ENERGY_SPECS if s["name"] == name))()


def pick_first(actions, info, count):
    if hasattr(actions, "action_for_candidate_indices"):
        return actions.action_for_candidate_indices([0])
    return list(actions)[-1]


@pytest.mark.parametrize("cost,good", [([T.DARK, T.DARK], True), ([T.PSYCHIC, T.DARK], True), ([T.PSYCHIC]*2, True), ([T.FIRE], False), ([T.DARK]*3, False), ([T.COLORLESS]*2, True)])
def test_rocket_energy_is_two_restricted_units(cost, good):
    s, p, o = board()
    c = p.active[0]
    c.name = "Team Rocket's Test"
    e = card("Team Rocket's Energy")
    c.attachment = [e]
    refresh_energy(c, s)
    assert len(c.energy) == 2 and check_energy(cost, c.energy) is good
    c.name = "Other"
    reconcile(s)
    assert e in p.discard and not c.energy


def test_berry_consumes_when_damage_prevented_and_ignoring_effects():
    for ignore in (False, True):
        s, p, o = board()
        source, target = p.active[0], o.active[0]
        source.cardType = T.WATER
        target.hp, target.weakness, target.resistance = 400, [], []
        berry = card("Passho Berry")
        target.attachment = [berry]
        target.attack_protection = {"turn": s.turn_number, "damage": True}
        a = AttackAction(p.id, source, source.attacks[0], target)
        a.attack.damage = 100
        drive(reduce_attack_damage(a, s, ignore_effects=ignore))
        assert berry in o.discard and berry not in target.attachment
        assert target.hp == (300 if ignore else 400)


def test_berry_reduces_damage_and_only_once():
    s, p, o = board()
    source, target = p.active[0], o.active[0]
    source.cardType = T.WATER
    target.hp, target.weakness, target.resistance = 500, [], []
    target.attachment = [card("Passho Berry")]
    for _ in range(2):
        a = AttackAction(p.id, source, source.attacks[0], target)
        a.attack.damage = 100
        drive(reduce_attack_damage(a, s))
    assert target.hp == 360


def test_super_potion_heals_then_discards_one_physical_energy():
    s, p, o = board()
    c = p.active[0]
    c.hp -= 80
    e = registry.get("SVE-008")()
    c.attachment = [e]
    t = card("Super Potion")
    zone(p, "hand", [t])
    old = c.hp
    drive(t.reduce_action(UseItemAction(p.id, t), s), pick_first)
    assert c.hp == old + 60 and e in p.discard and not c.attachment


def test_prize_ticket_preserves_order_of_original_deck_and_hides_new_prizes():
    s, p, o = board()
    original, prizes = list(p.left), list(p.prize)
    prizes[0].prize_face_up = True
    t = next(registry.get(x["effectKey"])() for x in TRAINER_SPECS if x["mechanic"].get("op") == "prize_ticket")
    zone(p, "hand", [t])
    drive(t.reduce_action(UseItemAction(p.id, t), s))
    assert p.prize == original[:6]
    assert p.left[:2] == original[6:] and set(p.left[2:]) == set(prizes)
    assert not any(getattr(c, "prize_face_up", False) for c in p.prize + p.left)


def test_rocket_robot_swaps_only_one_hand_card_and_keeps_prize_visible():
    s, p, o = board()
    t = next(registry.get(x["effectKey"])() for x in TRAINER_SPECS if x["mechanic"].get("op") == "rocket_robot")
    energy = registry.get("SVE-005")()
    zone(o, "hand", [energy])
    zone(p, "hand", [t])
    original = list(o.prize)
    drive(t.reduce_action(UseItemAction(p.id, t), s), pick_first)
    assert energy in o.prize and energy.prize_face_up
    assert len(o.prize) == 6 and len(o.hand) == 1 and o.hand[0] in original


def test_doll_changes_category_only_for_setup_and_gives_no_prizes():
    s, p, o = board()
    doll = card("Snorlax Doll")
    assert isinstance(doll, ItemCard) and not isinstance(doll, PokemonCard)
    zone(p, "hand", [doll])
    assert doll.get_actions(s) == [] and doll in RulesEngine._basic(p.hand)
    env = object.__new__(RulesEngine)
    env.gamestate = s
    p.active = []
    env._place(p, [doll], CardPosition.ACTIVE)
    doll.special_condition, doll.poisoned = SpecialCondition.ASLEEP, True
    reconcile(s)
    assert isinstance(doll, PokemonCard) and doll.hp == 120 and doll.prize == 0
    assert not getattr(doll, "poisoned", False) and not hasattr(doll, "special_condition")
    discard_pokemon(p, doll)
    reconcile(s)
    assert doll in p.discard and isinstance(doll, ItemCard) and not isinstance(doll, PokemonCard)


def test_tm_grants_attack_with_modified_cost_and_expires():
    from packages.rules.technical_machines import actions
    s, p, o = board()
    t = next(registry.get(x["effectKey"])() for x in TRAINER_SPECS if x["mechanic"].get("grantedAttack", {}).get("name") == "Crisis Punch")
    source = p.active[0]
    source.attachment = [t]
    source.energy = [T.COLORLESS] * 3
    assert not actions(t, s)
    o.prize = o.prize[:1]
    found = actions(t, s)
    assert len(found) == 1 and found[0].source is source
    source.energy = [T.COLORLESS] * 2
    assert not actions(t, s)
    end_turn_tools(s)
    assert t in p.discard and not source.attachment


def test_copy_supporter_does_not_play_or_discard_original():
    from packages.rules.supporter_copy import resolve
    s, p, o = board()
    supporter = card("Nemona")
    zone(o, "hand", [supporter])
    p.supporterPlayedTurn = True
    before = len(p.left)
    drive(resolve(p.active[0], s), pick_first)
    assert len(p.hand) == 3 and len(p.left) == before - 3
    assert supporter in o.hand and p.supporterPlayedTurn
    assert not p.discard


def test_boomerang_returns_after_own_attack_discard():
    from packages.rules.attack_attachments import begin, restore
    from packages.rules.zone_effects import discard_attached
    s, p, o = board()
    c, e = p.active[0], card("Boomerang Energy")
    c.attachment = [e]
    begin(SimpleNamespace(source=c), s)
    discard_attached(c, [e], p)
    assert e in p.discard
    restore(s)
    assert e in c.attachment and e not in p.discard


def test_damage_preview_cannot_consume_berry_on_a_later_zero_hit():
    from ptcg.core.reducer import _calculate_damage
    from packages.rules.damage_events import deal
    s, p, o = board()
    source, target = p.active[0], o.active[0]
    source.cardType = T.WATER
    berry = card("Passho Berry")
    target.attachment = [berry]
    _calculate_damage(source, target, 100, s)
    deal(source, target, 0, s)
    assert target.attachment == [berry] and berry not in o.discard


def test_groove_is_unavailable_when_active_pokemon_has_no_ability_attribute():
    from packages.rules.activated_effects import available
    s, p, o = board()
    assert not hasattr(p.active[0], "ability")
    source = next(registry.get(x["effectKey"])() for x in SPECS if any(a.get("requiresActiveAbility") == "Festival Lead" for a in x.get("abilities", [])))
    rule = next(a for a in source.spec["abilities"] if a.get("requiresActiveAbility"))
    assert not available(source, rule, s)


def test_copied_katy_defers_turn_transition_to_the_attack():
    from packages.rules.supporter_copy import resolve
    from ptcg.utils.utils import next_turn
    s, p, o = board()
    katy = card("Katy")
    zone(o, "hand", [katy])
    turn = s.turn_number
    drive(resolve(p.active[0], s), pick_first)
    assert s.turn == p.id and s.turn_number == turn
    next_turn(s)
    assert s.turn == o.id and s.turn_number == turn + 1 and katy in o.hand


@pytest.mark.parametrize("attack_name", ["Turbo Energize", "Blindside", "Devolution", "Crisis Punch", "Fluorite"])
def test_each_tool_attack_resolves_on_physical_holder(attack_name):
    from packages.rules.technical_machines import actions, resolve
    from packages.rules.invariants import physical_cards
    s, p, o = board()
    tool = next(registry.get(x["effectKey"])() for x in TRAINER_SPECS if x["mechanic"].get("grantedAttack", {}).get("name") == attack_name)
    holder, target = p.active[0], o.active[0]
    holder.attachment = [tool]
    holder.energy = [T.ANY] * 4
    target.hp, target.weakness, target.resistance = 500, [], []
    if attack_name == "Blindside":
        target.hp = 20
    if attack_name == "Crisis Punch":
        o.prize = o.prize[:1]
    before = len(physical_cards(s))
    available = actions(tool, s)
    assert available and available[0].source is holder
    turn = s.turn_number
    drive(resolve(tool, available[0], s), pick_first)
    assert s.turn_number == turn + 1 and tool in p.discard
    assert len(physical_cards(s)) == before

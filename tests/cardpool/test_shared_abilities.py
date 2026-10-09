from types import SimpleNamespace
from copy import deepcopy
import pytest
from packages.rules.plain import PlainPokemon, SPECS
from packages.rules.abilities import available, armor
from ptcg.core.card_registry import registry
from ptcg.core.reducer import _calculate_damage
from ptcg.utils.utils import next_turn, switch_pokemon
from test_effects import context, zone, drive


def board(rule):
    state, p, o = context()
    spec = deepcopy(SPECS[0])
    spec["abilities"] = [
        {"name": "Directed ability", "text": "", "trigger": "activated", **rule}
    ]
    source = type("DirectedAbilityPokemon", (PlainPokemon,), {"spec": spec})()
    zone(p, "active", [source])
    zone(p, "bench", [registry.get("P01-005")()])
    zone(p, "hand", [registry.get("SVE-008")() for _ in range(3)])
    zone(p, "left", [registry.get("SVE-008")() for _ in range(8)])
    return state, p, o, source


def activate(s, source):
    actions = available(source, s)
    assert len(actions) == 1
    drive(source.reduce_action(actions[0], s))


def test_psychic_embrace_can_repeat_but_never_knocks_out_recipient():
    from ptcg.core.enums import CardType

    rule = {
        "kind": "attach_energy",
        "origin": "discard",
        "type": "PSYCHIC",
        "targetType": "PSYCHIC",
        "counterCost": 20,
        "usageLimit": "unlimited",
    }
    s, p, o, c = board(rule)
    c.cardType = CardType.METAL
    recipient = p.bench[0]
    recipient.cardType = CardType.PSYCHIC
    recipient.hp = 50
    energies = [registry.get("SVE-005")() for _ in range(3)]
    zone(p, "discard", energies[:])
    activate(s, c)
    assert recipient.hp == 30 and available(c, s)
    activate(s, c)
    assert recipient.hp == 10 and not available(c, s)
    assert len(recipient.attachment) == 2 and len(p.discard) == 1
    assert not p.energyPlayedTurn and not c.abilityUsed


def test_attach_heal_still_attaches_when_recipient_has_no_damage():
    rule = {"kind": "attach_energy", "origin": "hand", "type": "GRASS", "heal": 30}
    s, p, o, c = board(rule)
    energy = registry.get("P4E-001")()
    zone(p, "hand", [energy])
    activate(s, c)
    assert energy in c.attachment + p.bench[0].attachment
    assert not available(c, s) and not p.energyPlayedTurn


def test_attachment_prefix_filter_rejects_other_owners_pokemon():
    rule = {
        "kind": "attach_energy",
        "origin": "hand",
        "type": "LIGHTNING",
        "prefix": "Iono's ",
        "usageLimit": "unlimited",
    }
    s, p, o, c = board(rule)
    zone(p, "hand", [registry.get("P4E-004")()])
    assert not available(c, s)
    p.bench[0].name = "Iono's Wattrel"
    activate(s, c)
    assert p.bench[0].attachment and not c.attachment


def test_continuous_damage_stacks_unless_explicitly_named_nonstacking():
    s, p, o, c = board(
        {"kind": "continuous", "trigger": "passive", "damage": 30, "noStack": "team"}
    )
    twin = type(c)()
    zone(p, "bench", [twin])
    target = registry.get("P01-005")()
    zone(o, "active", [target])
    target.weakness = target.resistance = []
    assert _calculate_damage(c, target, 40, s) == 70
    del c.spec["abilities"][0]["noStack"]
    assert _calculate_damage(c, target, 40, s) == 100


def test_continuous_armor_applies_after_weakness_and_stops_when_holder_leaves():
    s, p, o, c = board({"kind": "continuous", "trigger": "passive", "armor": 10})
    target = p.bench[0]
    zone(p, "active", [target])
    zone(p, "bench", [c])
    attacker = registry.get("P01-005")()
    zone(o, "active", [attacker])
    target.weakness = [attacker.cardType]
    target.resistance = []
    s.turn = o.id
    assert _calculate_damage(attacker, target, 40, s) == 70
    p.bench = []
    assert _calculate_damage(attacker, target, 40, s) == 80


def test_retreat_aura_free_overrides_increases_and_reverts_on_suppression():
    from packages.rules.modifiers import refresh_costs
    from ptcg.core.enums import Stage

    s, p, o, c = board(
        {
            "kind": "continuous",
            "trigger": "passive",
            "retreatFree": True,
            "stage": "basic",
        }
    )
    c.stage = Stage.BASIC
    refresh_costs(c, s)
    assert c.retreat == []
    blocker = registry.get("P01-005")()
    blocker.ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    zone(o, "active", [blocker])
    refresh_costs(c, s)
    assert c.retreat == type(c)().retreat


def test_ability_heal_all_and_recover_status_do_not_end_turn():
    s, p, o, c = board({"kind": "heal", "amount": 20, "all": True})
    c.hp -= 30
    p.bench[0].hp -= 10
    activate(s, c)
    assert c.hp == type(c)().hp - 10 and p.bench[0].hp == type(p.bench[0])().hp
    assert s.turn == p.id
    s, p, o, c = board({"kind": "recover_active_status"})
    assert not available(c, s)
    c.poisoned = True
    c.poison_damage = 60
    activate(s, c)
    assert not hasattr(c, "poisoned") and not hasattr(c, "poison_damage")


def test_draw_usage_is_per_physical_pokemon_and_switch_does_not_reset_it():
    s, p, o, source = board({"kind": "draw", "count": 1})
    activate(s, source)
    assert len(p.hand) == 4 and s.turn == p.id and not available(source, s)
    other = type(source)()
    zone(p, "bench", [other])
    assert available(other, s)
    switch_pokemon(source, other, p)
    assert not available(source, s)
    next_turn(s)
    next_turn(s)
    assert available(source, s)


def test_discard_cost_must_be_payable_and_is_paid_before_drawing():
    s, p, o, source = board({"kind": "discard_draw", "cost": 2, "count": 1})
    original = list(p.hand)
    activate(s, source)
    assert len(p.hand) == 2 and len([c for c in original if c in p.discard]) == 2
    source.abilityUsed = False
    p.hand = p.hand[:1]
    assert not available(source, s)


def test_attach_then_draw_does_not_consume_manual_attachment():
    s, p, o, source = board(
        {"kind": "attach_draw", "type": "PSYCHIC", "target": "bench", "count": 2}
    )
    energy = registry.get("SVE-005")()
    zone(p, "hand", [energy])
    activate(s, source)
    assert energy in p.bench[0].attachment and len(p.hand) == 2
    assert not p.energyPlayedTurn


def test_bottom_hand_is_shuffled_into_bottom_not_whole_deck():
    s, p, o, source = board({"kind": "bottom_hand_draw", "count": 1})
    hand, deck = list(p.hand), list(p.left)
    activate(s, source)
    assert p.hand == deck[:1] and p.left[:7] == deck[1:]
    assert set(p.left[7:]) == set(hand)


def test_switch_ability_can_be_used_from_bench():
    s, p, o, source = board({"kind": "switch"})
    old = p.bench[0]
    switch_pokemon(source, old, p)
    activate(s, source)
    assert p.active == [source] and source.abilityUsed


@pytest.mark.parametrize("bench", [False, True])
def test_armor_is_after_weakness_and_active_suppression_respects_zone(bench):
    s, p, o, source = board({"kind": "armor", "amount": 30, "trigger": "passive"})
    attacker = o.active[0]
    source.weakness = [attacker.cardType]
    assert _calculate_damage(attacker, source, 40, s) == 50
    attacker.ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    if bench:
        switch_pokemon(source, p.bench[0], p)
    assert armor(source, s) == (30 if bench else 0)


def test_active_suppression_removes_activated_ability():
    s, p, o, source = board({"kind": "draw", "count": 1})
    o.active[0].ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    assert not available(source, s)
    switch_pokemon(source, p.bench[0], p)
    assert available(source, s)


def test_self_counter_draw_finishes_draw_before_knockout():
    s, p, o, source = board({"kind": "self_counter_draw", "count": 1})
    source.hp = 10
    p.hasPokemonDead = False
    top = p.left[0]
    activate(s, source)
    assert top in p.hand and source in p.discard and len(o.prize) == 6 - source.prize
    assert not p.hasPokemonDead


def test_every_compiled_ability_can_be_described():
    for spec in SPECS:
        if spec.get("abilities"):
            source = registry.get(spec["effectKey"])()
            assert source.get_info()["abilities"]


def test_shared_search_limit_survives_another_physical_copy_and_resets_next_turn():
    s, p, o, c = board({"kind": "search_any", "count": 1, "sharedName": "Quick Search"})
    other = type(c)()
    zone(p, "bench", [other])
    activate(s, c)
    assert len(p.hand) == 4 and not s.public_reveals
    assert not available(other, s)
    next_turn(s)
    next_turn(s)
    assert available(other, s)


def test_active_only_heal_and_draw_until_have_useful_effect_requirements():
    s, p, o, c = board({"kind": "heal", "amount": 60, "activeOnly": True})
    assert not available(c, s)
    p.bench[0].hp -= 10
    activate(s, c)
    assert p.bench[0].hp == type(p.bench[0])().hp
    c.abilityUsed = False
    c.hp -= 10
    switch_pokemon(c, p.bench[0], p)
    assert not available(c, s)
    s, p, o, c = board({"kind": "draw_until", "count": 3})
    assert not available(c, s)
    zone(p, "hand", [])
    activate(s, c)
    assert len(p.hand) == 3

import pytest
from packages.rules.plain import TRAINER_SPECS
from ptcg.core.card_registry import registry
from ptcg.core.action import UseSupporterAction
from test_effects import context, zone, drive


def board(kind, first=False, name=None):
    name = name or {"draw": "Nemona", "both_shuffle_draw": "Judge"}.get(kind)
    spec = next(
        s
        for s in TRAINER_SPECS
        if s["mechanic"]["kind"] == kind
        and (bool(s["mechanic"].get("allowFirstTurn")) == first)
        and (name is None or s["name"] == name)
    )
    s, p, o = context()
    c = registry.get(spec["effectKey"])()
    p.firstTurn = False
    p.supporterPlayedTurn = False
    zone(p, "hand", [c, registry.get("SVE-008")(), registry.get("SVE-008")()])
    zone(p, "left", [registry.get("SVE-008")() for _ in range(10)])
    return s, p, o, c


def play(s, c):
    actions = c.get_actions(s)
    assert len(actions) == 1
    drive(c.reduce_action(actions[0], s))


def test_explorer_guide_discards_only_unselected_window_and_keeps_deck_tail():
    s, p, o, c = board("look_hand", name="Explorer's Guidance")
    before = list(p.left)
    play(s, c)
    assert p.left == before[6:]
    assert len([x for x in before[:6] if x in p.hand]) == 2
    assert len([x for x in before[:6] if x in p.discard]) == 4
    assert not s.public_reveals


def test_rika_moves_unselected_window_to_bottom_without_shuffling_tail():
    s, p, o, c = board("look_hand", name="Rika")
    before = list(p.left)
    play(s, c)
    assert p.left[:6] == before[4:]
    assert set(p.left[-2:]).issubset(set(before[:4]))
    assert all(x not in p.discard for x in before)


def test_hand_trimmer_opponent_chooses_first_then_owner_excluding_played_card():
    s, p, o, c = board("discard_hand_to", name="Hand Trimmer")
    zone(p, "hand", [c] + [registry.get("SVE-008")() for _ in range(7)])
    zone(o, "hand", [registry.get("SVE-008")() for _ in range(8)])
    seen = drive(c.reduce_action(c.get_actions(s)[0], s))
    actors = [x["raw_available_actions"][0].playerId for x in seen]
    assert actors == [o.id, p.id]
    assert len(p.hand) == len(o.hand) == 5 and c in p.discard


def test_unfair_stamp_uses_distinct_draw_counts_and_requires_previous_knockout():
    s, p, o, c = board("both_shuffle_draw", name="Unfair Stamp")
    p.hasPokemonDead = False
    assert not c.get_actions(s)
    p.hasPokemonDead = True
    play(s, c)
    assert len(p.hand) == 5 and len(o.hand) == 2


def test_bianca_filters_remaining_hp_and_heals_to_printed_maximum():
    s, p, o, c = board("heal", name="Bianca's Devotion")
    target = registry.get("P01-005")()
    zone(p, "active", [target])
    zone(p, "bench", [])
    target.hp = 40
    assert not c.get_actions(s)
    target.hp = 30
    play(s, c)
    assert target.hp == type(target)().hp


@pytest.mark.parametrize("kind,count", [("draw", 3), ("discard_draw", 7)])
def test_supporter_discard_and_draw_are_distinct_and_do_not_end_turn(kind, count):
    s, p, o, c = board(kind)
    old = list(p.hand[1:])
    play(s, c)
    assert c in p.discard and s.turn == p.id and p.supporterPlayedTurn
    assert len(p.hand) == count + (2 if kind == "draw" else 0)
    assert all((x in p.discard) == (kind == "discard_draw") for x in old)


def test_carmine_bypasses_first_turn_block_but_not_once_per_turn_limit():
    s, p, o, c = board("discard_draw", first=True)
    s.starting_player = p.id
    p.firstTurn = True
    p.supporterPlayedTurn = True
    assert any(
        isinstance(a, UseSupporterAction) and a.source is c for a in p.get_actions(s)
    )
    play(s, c)
    other = type(c)()
    zone(p, "hand", [other])
    assert not other.get_actions(s)
    assert not any(isinstance(a, UseSupporterAction) for a in p.get_actions(s))


def test_ordinary_supporter_cannot_be_played_in_starting_first_turn():
    s, p, o, c = board("draw")
    s.starting_player = p.id
    p.firstTurn = True
    assert not c.get_actions(s)


def test_judge_shuffles_both_hands_but_not_itself():
    s, p, o, c = board("both_shuffle_draw")
    hands = list(p.hand[1:]) + list(o.hand)
    play(s, c)
    assert len(p.hand) == len(o.hand) == 4 and c in p.discard
    assert all(
        any(x in zone_ for zone_ in (p.hand, p.left, o.hand, o.left)) for x in hands
    )


def test_pal_pad_requires_public_target_and_cannot_fail_to_find():
    s, p, o, c = board("recover_deck", name="Pal Pad")
    zone(p, "discard", [])
    assert not c.get_actions(s)
    supporter = registry.get("PAF-087")()
    zone(p, "discard", [supporter])
    seen = drive(c.reduce_action(c.get_actions(s)[0], s))
    assert supporter in p.left and c in p.discard
    assert all(all(a.chosen for a in item["raw_available_actions"]) for item in seen)


def test_search_pair_reveals_both_filters_and_allows_missing_categories():
    s, p, o, c = board("search_pair")
    item, tool = registry.get("PAF-084")(), registry.get("P01-002")()
    zone(p, "left", [item, tool, registry.get("SVE-008")()])
    play(s, c)
    assert item in p.hand and tool in p.hand
    assert len(s.public_reveals) == 2


def test_hidden_typed_search_may_fail_but_empty_deck_cannot_play():
    s, p, o, c = board("search_hand", name="Jacq")
    play(s, c)
    assert c in p.discard and len(p.left) == 10
    s, p, o, c = board("search_hand", name="Jacq")
    zone(p, "left", [])
    assert not c.get_actions(s)


def test_earthen_vessel_pays_cost_even_when_search_fails():
    s, p, o, c = board("search_hand", name="Earthen Vessel")
    before = list(p.hand[1:])
    play(s, c)
    assert c in p.discard and len([x for x in before if x in p.discard]) == 1
    # A hidden search may find zero cards, but its discard cost is mandatory.
    s, p, o, c = board("search_hand", name="Earthen Vessel")
    zone(p, "hand", [c])
    assert not c.get_actions(s)


def test_cassiopeia_requires_last_hand_and_does_not_reveal_untyped_search():
    s, p, o, c = board("search_hand", name="Cassiopeia")
    assert not c.get_actions(s)
    zone(p, "hand", [c])
    play(s, c)
    assert len(p.hand) == 2 and not s.public_reveals


def test_miriam_draw_requires_recovered_pokemon_and_does_not_end_turn():
    s, p, o, c = board("recover_deck", name="Miriam")
    zone(p, "discard", [])
    assert not c.get_actions(s)
    recovered = registry.get("P01-005")()
    zone(p, "discard", [recovered])
    play(s, c)
    assert len(p.hand) == 5 and recovered not in p.discard and s.turn == p.id


def test_katy_shuffles_remaining_hand_and_ends_turn():
    s, p, o, c = board("shuffle_draw", name="Katy")
    old = list(p.hand[1:])
    play(s, c)
    assert s.turn == o.id and len(p.hand) == 8 and c in p.discard
    assert all(x in p.hand + p.left for x in old)


def test_night_stretcher_recovers_without_shuffling_deck():
    s, p, o, c = board("recover_hand", name="Night Stretcher")
    recovered = registry.get("SVE-008")()
    zone(p, "discard", [recovered])
    old = list(p.left)
    play(s, c)
    assert recovered in p.hand and p.left == old


def test_boss_orders_choice_belongs_to_attacker_and_clears_conditions():
    s, p, o, c = board("gust")
    old = o.active[0]
    old.poisoned = True
    b = registry.get("P01-005")()
    zone(o, "bench", [b])
    seen = drive(c.reduce_action(c.get_actions(s)[0], s))
    assert o.active == [b] and old in o.bench and not hasattr(old, "poisoned")
    assert all(a.playerId == p.id for row in seen for a in row["raw_available_actions"])


@pytest.mark.parametrize("spec", TRAINER_SPECS, ids=lambda s: s["effectKey"])
def test_every_trainer_is_a_registered_class_with_matching_rule_text(spec):
    c = registry.get(spec["effectKey"])()
    assert c.name == spec["name"] and c.text == spec["text"]

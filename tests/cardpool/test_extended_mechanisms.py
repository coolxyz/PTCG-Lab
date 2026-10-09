import random
import pytest
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, SpecialCondition
from ptcg.utils.utils import next_turn
from packages.rules.attack_math import damage
from packages.rules.zone_effects import resolve
from tests.cardpool.test_field_effects import field_fixture
from tests.cardpool.test_shared_trainers import board, play
from test_effects import drive, zone


def test_discard_damage_counts_physical_energy_cards_not_provided_units():
    from packages.rules.discard_damage import resolve as discard_damage
    from ptcg.core.enums import Stage

    rule = {
        "kind": "discard_damage",
        "zone": "self",
        "mode": "multiply",
        "factor": 60,
        "maxCards": 4,
    }
    s, p, o, c, t, a = field_fixture(rule)
    c.stage = Stage.STAGE_2
    energy = [registry.get("P01-003")(), registry.get("SVE-008")()]
    c.attachment = energy[:]
    t.hp = 1000
    t.weakness = t.resistance = []
    drive(discard_damage(rule, a, s))
    assert t.hp == 880 and not c.attachment and all(e in p.discard for e in energy)


def test_discard_damage_basic_filter_excludes_special_energy():
    from packages.rules.discard_damage import resolve as discard_damage

    rule = {
        "kind": "discard_damage",
        "zone": "field",
        "mode": "multiply",
        "factor": 70,
        "basic": True,
    }
    s, p, o, c, t, a = field_fixture(rule)
    wild = registry.get("P01-004")()
    basic = registry.get("SVE-008")()
    c.attachment = [wild, basic]
    t.hp = 1000
    t.weakness = t.resistance = []
    drive(discard_damage(rule, a, s))
    assert t.hp == 930 and c.attachment == [wild] and basic in p.discard


def test_discard_damage_optional_zero_does_not_pay_or_deal_damage():
    from packages.rules.discard_damage import resolve as discard_damage

    rule = {"kind": "discard_damage", "zone": "self", "mode": "multiply", "factor": 70}
    s, p, o, c, t, a = field_fixture(rule)
    energy = registry.get("SVE-008")()
    c.attachment = [energy]
    before = t.hp
    gen = discard_damage(rule, a, s)
    item = next(gen)
    with pytest.raises(StopIteration):
        gen.send(next(x for x in item[3]["raw_available_actions"] if not x.chosen))
    assert c.attachment == [energy] and t.hp == before and s.turn == o.id


@pytest.mark.parametrize(
    "term,extra,expected",
    [
        ("opponent_status_count", {}, 3),
        ("has_status", {"owner": "opponent", "status": "POISONED"}, 1),
        ("opponent_small_hand", {"threshold": 5}, 1),
        ("own_small_deck", {"threshold": 3}, 0),
        ("own_field_count", {}, 2),
        ("opponent_ex_count", {}, 1),
    ],
)
def test_live_count_and_condition_terms(term, extra, expected):
    from ptcg.core.enums import PokemonType

    s, p, o, c, t, a = field_fixture({"kind": "draw", "count": 1})
    zone(p, "bench", [registry.get("P01-005")()])
    zone(p, "left", [registry.get("SVE-008")() for _ in range(5)])
    zone(o, "hand", [registry.get("SVE-008")() for _ in range(5)])
    t.pokemonType = PokemonType.EX
    t.poisoned = t.burned = True
    t.special_condition = SpecialCondition.CONFUSED
    value = damage({"term": term, "factor": 10, "mode": "multiply", **extra}, a, s)
    assert value == expected * 10


def test_typed_discard_count_excludes_energy_and_other_pokemon_types():
    s, p, o, c, t, a = field_fixture({"kind": "draw", "count": 1})
    psychic = registry.get("P01-005")()
    other = registry.get("P01-005")()
    psychic.cardType = CardType.PSYCHIC
    other.cardType = CardType.METAL
    zone(p, "discard", [psychic, other, registry.get("SVE-005")()])
    assert (
        damage(
            {
                "term": "typed_pokemon",
                "type": "PSYCHIC",
                "zone": "discard",
                "factor": 10,
                "mode": "multiply",
            },
            a,
            s,
        )
        == 10
    )


def test_energy_type_diversity_counts_basic_card_types_once_and_excludes_special():
    s, p, o, c, t, a = field_fixture({"kind": "draw", "count": 1})
    c.attachment = [
        registry.get(k)() for k in ("SVE-002", "SVE-002", "SVE-005", "P01-004")
    ]
    zone(p, "bench", [])
    assert (
        damage(
            {"term": "own_basic_energy_types", "factor": 50, "mode": "multiply"}, a, s
        )
        == 100
    )


def test_energy_distribution_updates_each_recipient_and_preserves_identity():
    rule = {"kind": "move_self_energy", "all": True, "distribute": True}
    s, p, o, c, t, a = field_fixture(rule)
    benches = [registry.get("P01-005")(), registry.get("P01-005")()]
    zone(p, "bench", benches)
    cards = [registry.get("SVE-008")(), registry.get("SVE-002")()]
    c.attachment = cards[:]
    gen = resolve(rule, a, s)
    item = next(gen)
    item = gen.send(
        next(x for x in item[3]["raw_available_actions"] if x.chosen == [benches[0]])
    )
    with pytest.raises(StopIteration):
        gen.send(
            next(
                x for x in item[3]["raw_available_actions"] if x.chosen == [benches[1]]
            )
        )
    assert not c.attachment and not c.energy
    assert benches[0].attachment == cards[:1] and benches[1].attachment == cards[1:]
    assert benches[0].energy == [CardType.METAL] and benches[1].energy == [
        CardType.FIRE
    ]


def test_item_lock_is_player_effect_not_tool_or_supporter_lock():
    from packages.rules.field_effects import resolve as field_resolve
    from packages.rules.core_fixes import end_turn_tools
    from ptcg.core.action import UseItemAction, UseToolAction, UseSupporterAction

    rule = {"kind": "item_lock"}
    s, p, o, c, t, a = field_fixture(rule)
    drive(field_resolve(rule, a, s))
    s.turn = o.id
    s.turn_number += 1
    o.firstTurn = False
    o.supporterPlayedTurn = False
    zone(o, "hand", [registry.get(k)() for k in ("PAF-084", "TEF-159", "PAF-087")])
    actions = o.get_actions(s)
    assert not any(isinstance(x, UseItemAction) for x in actions)
    assert any(isinstance(x, UseToolAction) for x in actions)
    assert any(isinstance(x, UseSupporterAction) for x in actions)
    end_turn_tools(s)
    assert any(isinstance(x, UseItemAction) for x in o.get_actions(s))


def test_gust_damage_hits_only_replacement_with_weakness():
    from packages.rules.field_effects import resolve as field_resolve

    rule = {"kind": "gust_damage", "amount": 30}
    s, p, o, c, t, a = field_fixture(rule)
    replacement = registry.get("P01-005")()
    replacement.weakness = [c.cardType]
    replacement.resistance = []
    zone(o, "bench", [replacement])
    old_hp = t.hp
    new_hp = replacement.hp
    drive(field_resolve(rule, a, s))
    assert o.active == [replacement] and replacement.hp == new_hp - 60
    assert t in o.bench and t.hp == old_hp


def test_random_return_reveals_only_selected_card_and_conserves_it():
    rule = {"kind": "random_discard_hand", "destination": "deck"}
    s, p, o, c, t, a = field_fixture(rule)
    before = set(o.hand + o.left)
    n = len(o.hand)
    drive(resolve(rule, a, s))
    assert len(o.hand) == n - 1 and set(o.hand + o.left) == before
    assert len(s.public_reveals) == 1 and len(s.public_reveals[0]["cards"]) == 1


def test_typed_energy_discard_counts_wildcards_but_not_unrelated_energy():
    s, p, o, c, t, a = field_fixture(
        {"kind": "discard_self_energy", "count": 2, "type": "FIRE"}
    )
    fire, metal, wild = [registry.get(k)() for k in ("SVE-002", "SVE-008", "P01-004")]
    c.attachment = [fire, metal, wild]
    c.dynamic_energy = True
    drive(resolve(c.spec["attacks"][0]["mechanic"], a, s))
    assert c.attachment == [metal] and fire in p.discard and wild in p.discard
    assert c.energy == [CardType.METAL]


@pytest.mark.parametrize("all_cards", [False, True])
def test_energy_move_preserves_physical_cards_and_refreshes_both_pokemon(all_cards):
    rule = {"kind": "move_self_energy", "all": all_cards}
    s, p, o, c, t, a = field_fixture(rule)
    b = registry.get("P01-005")()
    zone(p, "bench", [b])
    attached = [registry.get("SVE-008")() for _ in range(2)]
    c.attachment = attached[:]
    drive(resolve(rule, a, s))
    assert len(c.attachment) == (0 if all_cards else 1)
    assert len(b.attachment) == (2 if all_cards else 1)
    assert set(c.attachment + b.attachment) == set(attached) and not p.discard
    assert len(c.energy) + len(b.energy) == 2


def test_random_hand_discard_is_seeded_and_never_reveals_the_rest():
    rule = {"kind": "random_discard_hand"}
    s, p, o, c, t, a = field_fixture(rule)
    old = list(o.hand)
    s.rng = random.Random(19)
    expected = old[:]
    random.Random(19).shuffle(expected)
    drive(resolve(rule, a, s))
    assert o.discard[-1] is expected[0] and len(o.hand) == len(old) - 1
    assert not s.public_reveals


def test_drain_uses_final_damage_after_weakness_and_shield():
    s, p, o, c, t, a = field_fixture({"kind": "drain_damage"})
    c.hp = type(c)().hp - 60
    t.hp = 500
    t.weakness = [c.cardType]
    t.resistance = []
    t.damage_shield = {"turn": s.turn_number, "amount": 30}
    a.attack.damage = 40
    drive(c.reduce_action(a, s))
    assert t.hp == 450 and c.hp == type(c)().hp - 10


def test_discard_tools_happens_before_damage(monkeypatch):
    import ptcg.core.reducer as reducer

    s, p, o, c, t, a = field_fixture({"kind": "discard_tools_before_damage"})
    tool = registry.get("P01-002")()
    energy = registry.get("SVE-008")()
    t.attachment = [tool, energy]
    old = reducer._calculate_damage
    seen = []

    def calc(source, target, amount, state):
        assert tool in o.discard and target.attachment == [energy]
        seen.append(True)
        return old(source, target, amount, state)

    monkeypatch.setattr(reducer, "_calculate_damage", calc)
    drive(c.reduce_action(a, s))
    assert seen


def test_compound_status_retains_poison_alongside_sleep():
    rule = {
        "kind": "special_status",
        "status": "POISONED",
        "statuses": ["POISONED", "ASLEEP"],
        "target": "opponent",
        "coin": False,
    }
    s, p, o, c, t, a = field_fixture(rule)
    drive(c.resolve_mechanic(rule, a, s))
    assert t.poisoned and t.special_condition == SpecialCondition.ASLEEP


def test_opponent_attack_lock_expires_at_end_of_affected_turn():
    s, p, o, c, t, a = field_fixture({"kind": "opponent_attack_lock"})
    drive(c.reduce_action(a, s))
    assert s.turn == o.id and t.attack_blocked_turn == s.turn_number
    assert not any(type(x).__name__ == "AttackAction" for x in o.get_actions(s))
    next_turn(s)
    assert not hasattr(t, "attack_blocked_turn")


@pytest.mark.parametrize(
    "term,expected",
    [
        ("own_prizes", 6),
        ("opponent_prizes", 4),
        ("own_hand", 2),
        ("opponent_hand", 3),
        ("more_prizes", 1),
        ("equal_hands", 0),
        ("own_energy", 4),
        ("self_typed_energy", 2),
    ],
)
def test_added_expression_terms_count_independent_board_state(term, expected):
    s, p, o, c, t, a = field_fixture({"kind": "damage_expression"})
    zone(p, "hand", [registry.get("SVE-008")() for _ in range(2)])
    zone(o, "hand", [registry.get("SVE-008")() for _ in range(3)])
    o.prize = o.prize[:4]
    c.energy = [CardType.FIRE, CardType.ANY, CardType.METAL]
    b = registry.get("P01-005")()
    b.energy = [CardType.WATER]
    zone(p, "bench", [b])
    assert (
        damage({"term": term, "type": "FIRE", "mode": "multiply", "factor": 20}, a, s)
        == expected * 20
    )


def test_grusha_counts_energy_cards_attached_before_deciding_draw_limit():
    s, p, o, c = board("draw_until", name="Grusha")
    play(s, c)
    assert len(p.hand) == 7
    s, p, o, c = board("draw_until", name="Grusha")
    p.active[0].attachment = [registry.get("SVE-008")()]
    play(s, c)
    assert len(p.hand) == 5


def test_brassius_counts_hand_after_supporter_leaves_hand():
    s, p, o, c = board("shuffle_draw", name="Brassius")
    play(s, c)
    assert len(p.hand) == 3

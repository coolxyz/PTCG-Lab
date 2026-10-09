"""Independent expectations for shared zone rules, including copied attacks."""

import re
import pytest
from packages.rules.plain import PlainPokemon
from packages.rules.zone_effects import resolve, matches
from scripts.cardpool.compile_plain import zone_rule
from ptcg.core.action import AttackAction
from ptcg.core.card_registry import registry
from test_effects import context, zone, drive


def card(key="SVE-008"):
    return registry.get(key)()


def fixture(mechanic, damage=0):
    class Subject(PlainPokemon):
        spec = {
            "effectKey": "P4P-TEST",
            "name": "Subject",
            "hp": 80,
            "type": "METAL",
            "stage": "BASIC",
            "evolvesFrom": [],
            "weakness": [],
            "resistance": [],
            "retreat": 1,
            "attacks": [
                {"name": "Test", "cost": [], "damage": damage, "mechanic": mechanic}
            ],
        }

    state, p, o = context()
    source = Subject()
    zone(p, "active", [source])
    target = o.active[0]
    target.hp = 500
    target.weakness, target.resistance = [], []
    return (
        state,
        p,
        o,
        source,
        target,
        AttackAction(p.id, source, source.attacks[0], target),
    )


@pytest.mark.parametrize(
    "hand,left,expected", [(0, 10, 7), (5, 1, 6), (7, 10, 7), (9, 10, 9), (0, 0, 0)]
)
def test_draw_until_does_not_discard_or_overdraw(hand, left, expected):
    s, p, o, source, _, action = fixture({"kind": "draw_until", "count": 7})
    zone(p, "hand", [card() for _ in range(hand)])
    deck = zone(p, "left", [card() for _ in range(left)])[:]
    drive(source.reduce_action(action, s))
    assert len(p.hand) == expected
    assert p.left == deck[expected - hand :]
    assert s.turn == o.id


@pytest.mark.parametrize("hp,expected", [(20, 50), (70, 80), (80, 80)])
@pytest.mark.parametrize("damage", [0, 20])
def test_heal_uses_physical_source_and_caps_at_maximum(hp, expected, damage):
    s, _, _, source, target, action = fixture(
        {"kind": "heal_self", "amount": 30}, damage
    )
    source.hp = hp
    drive(source.reduce_action(action, s))
    assert source.hp == expected
    assert target.hp == 500 - damage


@pytest.mark.parametrize("left", [0, 1, 5])
def test_mill_moves_only_available_top_cards_and_does_not_win_early(left):
    s, p, o, source, _, action = fixture({"kind": "mill_opponent", "count": 2})
    deck = zone(o, "left", [card() for _ in range(left)])[:]
    before = list(o.discard)
    # Isolate the operation before the opponent's mandatory turn draw.
    drive(resolve(source.spec["attacks"][0]["mechanic"], action, s))
    assert o.discard == before + deck[:2]
    assert o.left == deck[2:]
    assert s.turn == p.id


@pytest.mark.parametrize(
    "optional,decline", [(False, False), (True, False), (True, True)]
)
@pytest.mark.parametrize("bench_size", [0, 2])
def test_self_switch_uses_owner_choice_and_preserves_optional_decline(
    optional, decline, bench_size
):
    s, p, _, source, _, action = fixture({"kind": "self_switch", "optional": optional})
    bench = zone(p, "bench", [card("P01-005") for _ in range(bench_size)])[:]
    source.retreat_blocked_turn = s.turn_number + 1
    seen = drive(
        source.reduce_action(action, s),
        lambda actions, info, n: next(a for a in actions if bool(a.chosen) != decline),
    )
    assert len(seen) == bool(bench_size)
    if bench and not decline:
        assert source in p.bench and p.active[0] in bench
        assert not hasattr(source, "retreat_blocked_turn")
    else:
        assert p.active == [source]


@pytest.mark.parametrize("owner", ["self", "opponent"])
@pytest.mark.parametrize("count", [1, "all"])
@pytest.mark.parametrize("attached", [0, 1, 3])
def test_discard_energy_preserves_tools_and_refreshes_physical_attachments(
    owner, count, attached
):
    mechanic = {"kind": f"discard_{owner}_energy", "count": count}
    s, p, o, source, target, action = fixture(mechanic)
    target, player = (source, p) if owner == "self" else (target, o)
    tool = card("P01-002")
    energies = [card("P01-004"), card(), card()][:attached]
    target.attachment = [tool] + energies
    drive(resolve(mechanic, action, s))
    expected = len(energies) if count == "all" else min(1, len(energies))
    assert len(target.attachment) == 1 + len(energies) - expected
    assert tool in target.attachment
    assert sum(c in player.discard for c in energies) == expected
    assert target.energy == [
        e for c in target.attachment if hasattr(c, "provides") for e in c.provides
    ]


@pytest.mark.parametrize(
    "kind,accepted,rejected",
    [
        ("pokemon", "P01-005", "SVE-008"),
        ("basic_energy", "SVE-008", "P01-004"),
        ("energy", "P01-004", "P01-002"),
        ("tool", "P01-002", "OBF-186"),
        ("stadium", "PAL-171", "PAF-084"),
    ],
)
@pytest.mark.parametrize("decline", [False, True])
def test_search_hand_filters_and_only_reveals_selected_cards(
    kind, accepted, rejected, decline
):
    mechanic = {"kind": "search_hand", "filter": kind, "count": 2}
    s, p, _, source, _, action = fixture(mechanic)
    good, bad = card(accepted), card(rejected)
    zone(p, "left", [bad, good])
    before = len(s.public_reveals)
    assert matches(good, kind) and not matches(bad, kind)
    drive(
        source.reduce_action(action, s),
        lambda actions, info, n: next(a for a in actions if bool(a.chosen) != decline),
    )
    assert bad in p.left and bad not in p.hand
    if decline:
        assert good in p.left and len(s.public_reveals) == before
    else:
        assert good in p.hand and good not in p.left
        revealed = s.public_reveals[before:]
        assert len(revealed) == 1 and len(revealed[0]["cards"]) == 1
        assert revealed[0]["cards"][0] == good.to_dict()


@pytest.mark.parametrize("bench_count", [0, 4, 5])
def test_search_bench_obeys_capacity_and_never_selects_evolutions(bench_count):
    mechanic = {"kind": "search_bench", "count": 2}
    s, p, _, source, _, action = fixture(mechanic)
    zone(p, "bench", [card("P01-005") for _ in range(bench_count)])
    basics = [card("P01-006"), card("P01-005")]
    evolved = card("TWM-129")
    zone(p, "left", [*basics, evolved, card()])
    drive(resolve(mechanic, action, s))
    assert len(p.bench) == min(5, bench_count + 2)
    assert evolved in p.left
    assert all(c.firstTurnPlayed for c in basics if c in p.bench)


@pytest.mark.parametrize(
    "mechanic",
    [
        {"kind": "heal_self", "amount": 30},
        {"kind": "discard_self_energy", "count": 1},
        {"kind": "self_switch", "optional": False},
    ],
)
def test_mew_copy_changes_itself_not_the_original_card(mechanic):
    from packages.rules.effects import Mew

    s, p, o, original, _, _ = fixture(mechanic)
    mew = Mew()
    mew.hp = 100
    own_energy, enemy_energy = card(), card()
    mew.attachment, original.attachment = [own_energy], [enemy_energy]
    zone(p, "active", [mew])
    zone(p, "bench", [card("P01-005")])
    zone(o, "active", [original])
    original_hp = original.hp
    drive(
        mew._genome_hacking_attack(AttackAction(p.id, mew, mew.attacks[0], original), s)
    )
    assert original.hp == original_hp and original.attachment == [enemy_energy]
    if mechanic["kind"] == "heal_self":
        assert mew.hp == 130
    elif mechanic["kind"] == "discard_self_energy":
        assert mew.attachment == [] and own_energy in p.discard
    else:
        assert mew in p.bench


@pytest.mark.parametrize(
    "en,cn",
    [
        (
            "Draw cards until you have 7 cards in your hand.",
            "从牌库上方抽取卡牌，直到自己的手牌变为7张为止。",
        ),
        ("Heal 30 damage from this Pokémon.", "回复这只宝可梦「30」HP。"),
        (
            "Search your deck for up to 2 Basic Energy cards, reveal them, and put them into your hand. Then, shuffle your deck.",
            "选择自己牌库中最多2张基本能量，在给对手看过之后，加入手牌。并重洗牌库。",
        ),
    ],
)
def test_closed_grammar_requires_complete_bilingual_agreement(en, cn):
    assert zone_rule(en, cn)
    assert zone_rule(en + " Then, draw a card.", cn) is None
    assert zone_rule(en, cn + "然后抽1张卡。") is None
    assert zone_rule(re.sub(r"\d+", "99", en), cn) is None


@pytest.mark.parametrize("target_kind", ["self", "any"])
@pytest.mark.parametrize("decline", [False, True])
def test_search_attach_filters_basic_type_and_does_not_consume_manual_attachment(
    target_kind, decline
):
    mechanic = {
        "kind": "search_attach",
        "count": 2,
        "target": target_kind,
        "type": "METAL",
    }
    s, p, _, source, _, action = fixture(mechanic)
    bench = card("P01-005")
    zone(p, "bench", [bench])
    energies = [card(), card()]
    invalid = [card("P01-004"), card("SVE-002")]
    zone(p, "left", energies + invalid)
    p.energyPlayedTurn = False

    def choose(actions, info, n):
        if n == 1 and decline:
            return next(a for a in actions if not a.chosen)
        return list(actions)[-1]

    drive(resolve(mechanic, action, s), choose)
    assert not p.energyPlayedTurn
    assert all(c in p.left for c in invalid)
    target = source if target_kind == "self" else bench
    if decline:
        assert all(c in p.left for c in energies)
    else:
        assert target.attachment == energies
        assert target.energy == [c.cardType for c in energies]
        assert not any(c in p.left for c in energies)


def test_multiple_energy_units_are_not_mistaken_for_physical_cards():
    assert (
        zone_rule(
            "Discard 2 Energy from this Pokémon.",
            "选择这只宝可梦身上附着的2个能量，放于弃牌区。",
        )
        == {'kind': 'discard_self_energy', 'count': 2}
    )


@pytest.mark.parametrize('extra', [False, True])
def test_two_unit_payment_allows_one_multi_energy_or_multi_plus_basic(extra):
    from ptcg.core.enums import Stage
    s, p, o, source, _, action = fixture({'kind': 'discard_self_energy', 'count': 2})
    source.stage = Stage.STAGE_2
    multi, basic, spare = card('P01-003'), card(), card()
    source.attachment = [multi, basic, spare]
    def pick(actions, info, step):
        wanted = [multi] if step == 1 else ([basic] if extra else [])
        return next(a for a in actions if a.chosen == wanted)
    drive(resolve({'kind': 'discard_self_energy', 'count': 2}, action, s), pick)
    assert multi in p.discard and (basic in p.discard) == extra
    assert spare in source.attachment


def test_two_unit_payment_cannot_stop_after_only_one_basic_energy():
    s, p, o, source, _, action = fixture({'kind': 'discard_self_energy', 'count': 2})
    source.attachment = [card(), card(), card()]
    def pick(actions, info, step):
        assert all(len(a.chosen) == 1 for a in actions)
        return list(actions)[0]
    drive(resolve({'kind': 'discard_self_energy', 'count': 2}, action, s), pick)
    assert len(source.attachment) == 1 and len([c for c in p.discard if c.name == 'Metal Energy']) == 2

from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, Stage
from packages.rules.abilities import available
from tests.cardpool.test_shared_abilities import board, activate
from test_effects import zone, drive


def test_cursed_blast_knocks_out_both_sides_before_prize_selection():
    s, p, o, c = board({"kind": "activated_effect", "selfKnockout": True, "effect": {"kind": "counters", "zone": "field", "count": 1, "amount": 130}})
    c.prize = 1
    target = registry.get("P01-005")()
    zone(o, "active", [registry.get("P01-006")()])
    zone(o, "bench", [target])
    activate(s, c)
    assert c in p.discard and target in o.discard
    assert len(p.prize) == len(o.prize) == 5 and s.turn == p.id


def test_counter_buff_is_temporary_and_survives_later_suppression():
    from types import SimpleNamespace
    from packages.rules.modifiers import attack_damage
    s, p, o, c = board({"kind": "activated_effect", "effect": {"kind": "counter_buff", "selfCounters": 50, "damageBonus": 120}})
    c.hp = 200
    activate(s, c)
    assert c.hp == 150
    o.active[0].ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    assert attack_damage(c, o.active[0], 100, s) == 220
    s.turn_number += 2
    assert attack_damage(c, o.active[0], 100, s) == 100


def test_squawk_first_personal_turn_and_shared_name_limit():
    rule = {
        "kind": "activated_effect",
        "firstTurnOnly": True,
        "sharedName": "Squawk and Seize",
        "effect": {"kind": "discard_hand_draw", "count": 6},
    }
    s, p, o, c = board(rule)
    assert not available(c, s)
    p.firstTurn = True
    other = type(c)()
    zone(p, "bench", [other])
    old_hand = list(p.hand)
    activate(s, c)
    assert len(p.hand) == 6 and all(card in p.discard for card in old_hand)
    assert not available(other, s)


def test_bouquet_requires_grass_cost_and_counters_can_knock_out():
    s, p, o, c = board(
        {
            "kind": "activated_effect",
            "discardBasicType": "GRASS",
            "effect": {"kind": "counters", "zone": "bench", "count": 1, "amount": 30},
        }
    )
    target = registry.get("P01-005")()
    target.hp = 20
    zone(o, "bench", [target])
    assert not available(c, s)
    payment = registry.get("P4E-001")()
    zone(p, "hand", [payment])
    activate(s, c)
    assert payment in p.discard and target in o.discard
    assert len(p.prize) == 5 and s.turn == p.id


def test_koraidon_only_attaches_to_basic_fighting_and_ends_turn():
    s, p, o, c = board(
        {
            "kind": "activated_effect",
            "endTurn": True,
            "effect": {
                "kind": "attach",
                "origin": "discard",
                "count": 2,
                "basic": True,
                "type": "FIGHTING",
                "targetType": "FIGHTING",
                "targetStage": "BASIC",
            },
        }
    )
    c.cardType, c.stage = CardType.FIGHTING, Stage.BASIC
    p.bench[0].cardType, p.bench[0].stage = CardType.FIGHTING, Stage.STAGE_1
    energy = [registry.get("P4E-006")() for _ in range(2)]
    zone(p, "discard", list(energy))
    activate(s, c)
    assert c.attachment == energy and not p.bench[0].attachment
    assert s.turn == o.id


def test_subjugating_chains_filters_target_and_poisons_new_active():
    s, p, o, c = board(
        {
            "kind": "activated_effect",
            "sharedName": "Subjugating Chains",
            "effect": {
                "kind": "switch_poison",
                "type": "DARK",
                "exceptName": "Pecharunt ex",
            },
        }
    )
    target = p.bench[0]
    target.cardType = CardType.DARK
    target.name = "Pecharunt ex"
    assert not available(c, s)
    target.name = "Eligible"
    activate(s, c)
    assert p.active == [target] and target.poisoned
    assert target.poison_damage == 10
    assert not available(c, s)


def test_hyper_blower_defender_chooses_and_discards_source_without_prize():
    s, p, o, c = board(
        {
            "kind": "activated_effect",
            "benchOnly": True,
            "effect": {"kind": "opponent_switch_discard_self"},
        }
    )
    assert not available(c, s)
    zone(p, "active", [registry.get("P01-005")()])
    zone(p, "bench", [c])
    replacement = registry.get("P01-006")()
    zone(o, "bench", [replacement])
    energy = registry.get("SVE-008")()
    c.attachment = [energy]
    prizes = len(o.prize)
    prompts = drive(c.reduce_action(available(c, s)[0], s))
    assert prompts[0]["raw_available_actions"][0].playerId == o.id
    assert c in p.discard and energy in p.discard
    assert o.active == [replacement] and len(o.prize) == prizes


def test_sandy_shocks_requires_prize_threshold_and_matching_energy():
    s, p, o, c = board(
        {
            "kind": "activated_effect",
            "maxOpponentPrizes": 4,
            "effect": {
                "kind": "attach",
                "origin": "discard",
                "count": 1,
                "basic": True,
                "type": "FIGHTING",
                "target": "self",
            },
        }
    )
    energy = registry.get("P4E-006")()
    zone(p, "discard", [energy])
    assert not available(c, s)
    o.prize = o.prize[:4]
    activate(s, c)
    assert c.attachment == [energy] and s.turn == p.id

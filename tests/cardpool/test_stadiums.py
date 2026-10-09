import random
import pytest
from ptcg.core.action import PutStadiumAction, UseStadiumAction, DiscardStadiumAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, Stage
from packages.rules.plain import TRAINER_SPECS
from packages.rules.modifiers import refresh_costs, attack_damage, armor
from packages.rules.maximum_hp import reconcile, maximum
from tests.cardpool.test_checkup import board
from test_effects import zone, drive


def stadium(name):
    return registry.get(
        next(s["effectKey"] for s in TRAINER_SPECS if s["name"] == name)
    )()


def field(s, p, name):
    c = stadium(name)
    s.stadium = [c]
    c.playedFrom = p.id
    p.stadiumUsedTurn = False
    return c


def test_stadium_replacement_goes_to_original_owner_and_same_name_is_illegal():
    s, p, o = board()
    old = field(s, o, "Beach Court")
    new = stadium("Practice Studio")
    zone(p, "hand", [new, stadium("Beach Court")])
    p.stadiumPlayedTurn = False
    assert not p.hand[1].get_actions(s)
    drive(new.reduce_action(PutStadiumAction(p.id, new), s))
    assert s.stadium == [new] and old in o.discard and old not in p.discard
    assert p.stadiumPlayedTurn and new.playedFrom == p.id


@pytest.mark.parametrize("name", ["Town Store", "Mesagoza", "Spikemuth Gym"])
def test_private_search_can_fail_but_use_is_spent_and_both_players_can_use(name):
    s, p, o = board()
    c = field(s, p, name)
    s.rng = random.Random(1)
    assert c.get_actions(s)
    drive(c.reduce_action(UseStadiumAction(p.id, c), s))
    assert not c.get_actions(s) and p.stadiumUsedTurn
    assert not s.public_reveals
    s.turn = o.id
    o.stadiumUsedTurn = False
    assert c.get_actions(s)


def test_moonlit_heals_all_own_after_psychic_energy_cost():
    s, p, o = board()
    c = field(s, p, "Moonlit Hill")
    psychic = registry.get("SVE-005")()
    zone(p, "hand", [psychic])
    before = [x.hp for x in p.active + p.bench]
    for x in p.active + p.bench:
        x.hp -= 50
    drive(c.reduce_action(UseStadiumAction(p.id, c), s))
    assert psychic in p.discard
    assert [x.hp for x in p.active + p.bench] == [n - 20 for n in before]


def test_night_academy_keeps_card_private_and_places_at_top_not_bottom():
    s, p, o = board()
    c = field(s, p, "Academy at Night")
    card = registry.get("SVE-002")()
    zone(p, "hand", [card])
    drive(c.reduce_action(UseStadiumAction(p.id, c), s))
    assert p.left[0] is card and not p.hand and not s.public_reveals


def test_league_headquarters_cost_and_beach_retreat_restore_on_replacement():
    s, p, o = board()
    x = p.active[0]
    x.stage = Stage.BASIC
    original = list(type(x)().attacks[0].cost)
    field(s, p, "Pokémon League Headquarters")
    refresh_costs(x, s)
    assert x.attacks[0].cost == original + [CardType.COLORLESS]
    field(s, p, "Beach Court")
    refresh_costs(x, s)
    assert x.attacks[0].cost == original and x.retreat == type(x)().retreat[1:]
    s.stadium = []
    refresh_costs(x, s)
    assert x.retreat == type(x)().retreat


def test_stage2_max_hp_preserves_counters_on_both_sides_and_restores():
    s, p, o = board()
    cards = [p.active[0], o.bench[0]]
    for x in cards:
        x.stage = Stage.STAGE_2
        x.hp -= 20
    field(s, p, "Gravity Mountain")
    reconcile(s)
    assert all(
        maximum(x) == type(x)().hp - 30 and x.hp == type(x)().hp - 50 for x in cards
    )
    s.stadium = []
    reconcile(s)
    assert all(x.hp == type(x)().hp - 20 for x in cards)


def test_stadium_damage_only_opponent_active_armor_only_opponent_damage():
    s, p, o = board()
    x = p.active[0]
    x.stage = Stage.STAGE_1
    field(s, p, "Practice Studio")
    assert attack_damage(x, o.active[0], 40, s) == 50
    assert attack_damage(x, o.bench[0], 40, s) == 40
    field(s, p, "Full Metal Lab")
    x.cardType = CardType.METAL
    assert armor(x, s, o.active[0]) == 30
    assert armor(x, s, p.bench[0]) == 0


def test_watchtower_suppresses_colorless_only_and_restores():
    from packages.rules.abilities import enabled

    s, p, o = board()
    x, y = p.active[0], o.active[0]
    x.cardType, y.cardType = CardType.COLORLESS, CardType.PSYCHIC
    field(s, p, "Team Rocket's Watchtower")
    assert not enabled(x, s) and enabled(y, s)
    s.stadium = []
    assert enabled(x, s)


def test_replacing_used_stadium_allows_the_new_effect_in_same_turn():
    s, p, o = board()
    old = field(s, p, "Town Store")
    drive(old.reduce_action(UseStadiumAction(p.id, old), s))
    new = stadium("Academy at Night")
    zone(p, "hand", [new, registry.get("SVE-002")()])
    p.stadiumPlayedTurn = False
    drive(new.reduce_action(PutStadiumAction(p.id, new), s))
    assert any(
        isinstance(a, UseStadiumAction) and a.source is new for a in p.get_actions(s)
    )
    drive(new.reduce_action(UseStadiumAction(p.id, new), s))
    assert not new.get_actions(s)


def test_festival_grounds_requires_physical_energy_and_cures_all_conditions():
    from ptcg.core.enums import SpecialCondition
    s, p, o = board()
    x, y = p.active[0], o.active[0]
    field(s, p, "Festival Grounds")
    x.attachment = [registry.get("SVE-002")()]
    for c in (x, y):
        c.special_condition = SpecialCondition.ASLEEP
        c.poisoned = c.burned = True
    reconcile(s)
    assert not getattr(x, "poisoned", False) and not hasattr(x, "special_condition")
    assert y.poisoned and y.burned and y.special_condition == SpecialCondition.ASLEEP
    x.attachment = []
    x.poisoned = True
    reconcile(s)
    assert x.poisoned


def test_factory_requires_rocket_supporter_this_turn_and_artazon_excludes_rule_box():
    from packages.rules.stadiums import matches
    from ptcg.core.enums import PokemonType
    s, p, o = board()
    c = field(s, p, "Team Rocket's Factory")
    assert not c.get_actions(s)
    p.rocket_supporter_turn = s.turn_number
    assert c.get_actions(s)
    count = len(p.left)
    drive(c.reduce_action(UseStadiumAction(p.id, c), s))
    assert len(p.left) == count - 2
    p.stadiumUsedTurn = False
    s.turn_number += 2
    assert not c.get_actions(s)
    x = registry.get("P01-005")()
    x.stage, x.pokemonType = Stage.BASIC, PokemonType.NORMAL
    assert matches(x, "basic_no_rule")
    x.pokemonType = PokemonType.EX
    assert not matches(x, "basic_no_rule")

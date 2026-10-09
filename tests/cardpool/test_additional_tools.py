from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, Stage
from ptcg.core.action import AttackAction
from ptcg.core.reducer import _calculate_damage
from packages.rules.plain import TRAINER_SPECS
from packages.rules.maximum_hp import reconcile, maximum
from packages.rules.damage_events import deal, finish
from tests.cardpool.test_checkup import board
from tests.cardpool.test_stadiums import field
from test_effects import drive


def tool(name):
    return registry.get(next(s["effectKey"] for s in TRAINER_SPECS if s["name"] == name))()


def test_luxury_bomb_is_consumed_once_even_with_ability_suppressed():
    s, p, o = board()
    target, source = o.active[0], p.active[0]
    bomb = tool("Deluxe Bomb")
    target.attachment = [bomb]
    target.ability_blocked_turn = s.turn_number
    before = source.hp
    deal(source, target, 10, s)
    finish(s)
    assert source.hp == before - 120
    assert bomb in o.discard and not target.attachment
    deal(source, target, 10, s)
    finish(s)
    assert source.hp == before - 120


def test_survival_brace_consumes_only_on_opponents_lethal_full_hp_damage():
    s, p, o = board()
    target = o.active[0]
    brace = tool("Survival Brace")
    target.attachment = [brace]
    deal(p.active[0], target, target.hp, s)
    finish(s)
    assert target.hp == 10 and brace in o.discard


def test_sparkling_crystal_can_waive_missing_typed_energy_and_restores_at_tower():
    from ptcg.core.enums import PokemonRule
    from packages.rules.modifiers import refresh_costs
    from packages.rules.plain import SPECS
    from test_effects import zone
    s, p, o = board()
    spec = next(x for x in SPECS if x.get("pokemonRule") == "TERA" and any(len(set(a["cost"]) - {"COLORLESS"}) >= 2 for a in x["attacks"]))
    card = registry.get(spec["effectKey"])()
    zone(p, "active", [card])
    card.attachment = [tool("Sparkling Crystal")]
    card.energy = []
    printed = [len(a.cost) for a in card.attacks]
    refresh_costs(card, s)
    assert [len(a.cost) for a in card.attacks] == [max(0, n - 1) for n in printed]
    field(s, p, "Jamming Tower")
    refresh_costs(card, s)
    assert [len(a.cost) for a in card.attacks] == printed


def test_ancient_capsule_cures_under_ability_lock_and_loses_both_effects_at_tower():
    from ptcg.core.enums import PokemonRule, SpecialCondition
    s, p, o = board()
    c = p.active[0]
    c.pokemonRule = PokemonRule.ANCIENT
    base = maximum(c)
    c.hp -= 20
    c.ability_blocked_turn = s.turn_number
    c.attachment = [tool("Ancient Booster Energy Capsule")]
    c.poisoned, c.special_condition = True, SpecialCondition.ASLEEP
    reconcile(s)
    assert maximum(c) == base + 60 and c.hp == base + 40
    assert not getattr(c,"poisoned",False) and getattr(c,"special_condition",SpecialCondition.NONE) == SpecialCondition.NONE
    field(s,p,"Jamming Tower")
    c.poisoned = True
    reconcile(s)
    assert maximum(c) == base and c.hp == base - 20 and c.poisoned


def test_safety_goggles_weakness_is_live_and_basic_only():
    s, p, o = board()
    source, target = p.active[0], o.active[0]
    target.stage = Stage.BASIC
    target.weakness = [source.cardType]
    target.attachment = [tool("Protective Goggles")]
    assert _calculate_damage(source, target, 50, s) == 50
    target.stage = Stage.STAGE_1
    assert _calculate_damage(source, target, 50, s) == 100
    target.stage = Stage.BASIC
    field(s, p, "Jamming Tower")
    assert _calculate_damage(source, target, 50, s) == 100
    assert len(target.attachment) == 1


def test_helmet_damage_reaction_is_suppressed_by_tower_not_ability_suppression():
    s, p, o = board()
    source, target = p.active[0], o.active[0]
    target.attachment = [tool("Rocky Helmet")]
    target.ability_blocked_turn = s.turn_number
    previous = source.hp
    deal(source, target, 10, s)
    finish(s)
    assert source.hp == previous - 20
    field(s, p, "Jamming Tower")
    deal(source, target, 10, s)
    finish(s)
    assert source.hp == previous - 20


def test_vengeful_punch_requires_attack_damage_knockout_and_cape_adds_prize():
    s, p, o = board()
    source, target = p.active[0], o.active[0]
    target.attachment = [tool("Vengeful Punch")]
    previous = source.hp
    deal(source, target, 10, s)
    finish(s)
    assert source.hp == previous
    deal(source, target, 1000, s)
    finish(s)
    assert source.hp == previous - 40
    s, p, o = board()
    target = o.active[0]
    target.attachment = [tool("Luxurious Cape")]
    reconcile(s)
    assert maximum(target) == type(target)().hp + 100
    deal(p.active[0], target, 1000, s)
    finish(s)
    assert target.knockout_prize_reduction == -1


def test_tower_removes_tool_hp_preserving_damage_and_restore_reapplies():
    s, p, o = board()
    target = p.active[0]
    target.hp -= 10
    target.attachment = [tool("Luxurious Cape")]
    reconcile(s)
    before = target.hp
    field(s, p, "Jamming Tower")
    reconcile(s)
    assert target.hp == before - 100
    s.stadium = []
    reconcile(s)
    assert target.hp == before


def test_leftovers_heals_before_checkup_only_own_active_once():
    from ptcg.utils.utils import next_turn
    from packages.rules.checkup import finish as checkup
    s, p, o = board()
    target = p.active[0]
    target.attachment = [tool("Leftovers")]
    target.hp = 10
    target.poisoned, target.poison_damage = True, 20
    other = o.active[0]
    other.attachment = [tool("Leftovers")]
    other.hp = 10
    next_turn(s)
    assert target.hp == 30 and other.hp == 10
    drive(checkup(s))
    assert target.hp == 10 and other.hp == 10


def test_tower_disables_legacy_rescue_board_and_attached_tm_but_not_attachment():
    from packages.rules.effects import TechnicalMachine
    s, p, o = board()
    target = p.active[0]
    target.attachment = [registry.get("TEF-159")()]
    p.get_actions(s)
    assert len(target.retreat) == max(0, len(type(target)().retreat) - 1)
    field(s, p, "Jamming Tower")
    p.get_actions(s)
    assert target.retreat == type(target)().retreat
    tm = TechnicalMachine()
    target.attachment = [tm]
    target.energy = [CardType.COLORLESS]
    assert not any(isinstance(a, AttackAction) and getattr(a, "effect_source", None) is tm for a in p.get_actions(s))
    from packages.rules.core_fixes import end_turn_tools
    end_turn_tools(s)
    assert tm in target.attachment

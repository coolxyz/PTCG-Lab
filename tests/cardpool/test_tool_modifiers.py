import pytest
from ptcg.core.card import ToolCard
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, PokemonType, PokemonRule, Stage
from ptcg.core.reducer import _calculate_damage
from packages.rules.trainers import CompiledTrainer
from packages.rules.modifiers import refresh_costs
from scripts.cardpool.trainer_rules import compile_trainer
from tests.wiki_fixtures import load_articles
from test_effects import context, zone, drive


def attach(title, state, player, target):
    spec = compile_trainer(load_articles()[title + "（TCG）"])
    assert spec is not None
    spec["effectKey"] = "P4T-TEST"
    cls = type("TestTool", (CompiledTrainer, ToolCard), {"spec": spec})
    tool = cls()
    zone(player, "hand", [tool])
    action = next(a for a in tool.get_actions(state) if a.target is target)
    drive(tool.reduce_action(action, state))
    assert tool in target.attachment and tool not in player.discard
    assert not tool.get_actions(state)
    return tool


def board():
    s, p, o = context()
    a, b = registry.get("P01-005")(), registry.get("P01-005")()
    zone(p, "active", [a])
    zone(o, "active", [b])
    b.weakness, b.resistance = [], []
    return s, p, o, a, b


def test_tool_bonus_precedes_weakness_and_never_creates_damage():
    s, p, o, a, b = board()
    attach("活力头带", s, p, a)
    b.weakness = [a.cardType]
    assert _calculate_damage(a, b, 30, s) == 80
    assert _calculate_damage(a, b, 0, s) == 0
    zone(o, "bench", [b])
    o.active = []
    assert _calculate_damage(a, b, 30, s) == 60


def test_defiance_vest_is_dynamic_after_weakness_and_opponent_only():
    s, p, o, a, b = board()
    s.turn = o.id
    attach("不服输背心", s, o, b)
    s.turn = p.id
    o.prize = [object()] * 6
    p.prize = [object()] * 5
    b.weakness = [a.cardType]
    assert _calculate_damage(a, b, 50, s) == 60
    p.prize.append(object())
    assert _calculate_damage(a, b, 50, s) == 100


@pytest.mark.parametrize(
    "title,category,bonus",
    [("讲究腰带", PokemonType.V, 30), ("極限腰帶", PokemonType.EX, 50)],
)
def test_target_class_filters(title, category, bonus):
    s, p, o, a, b = board()
    attach(title, s, p, a)
    assert _calculate_damage(a, b, 30, s) == 30
    b.pokemonType = category
    assert _calculate_damage(a, b, 30, s) == 30 + bonus


def test_future_tool_requires_future_and_detachment_restores_retreat():
    s, p, o, a, b = board()
    tool = attach("驅勁能量 未來", s, p, a)
    original = list(a.retreat)
    refresh_costs(a, s)
    assert a.retreat == original and _calculate_damage(a, b, 30, s) == 30
    a.pokemonRule = PokemonRule.FUTURE
    refresh_costs(a, s)
    assert a.retreat == [] and _calculate_damage(a, b, 30, s) == 50
    a.attachment.remove(tool)
    refresh_costs(a, s)
    assert a.retreat == original and _calculate_damage(a, b, 30, s) == 30


def test_hop_tool_removes_only_colorless_and_restores_cost():
    s, p, o, a, b = board()
    tool = attach("赫普的講究頭帶", s, p, a)
    original = [list(attack.cost) for attack in a.attacks]
    a.name = "Hop's Test"
    refresh_costs(a, s)
    for attack, old in zip(a.attacks, original):
        expected = list(old)
        if CardType.COLORLESS in expected:
            expected.remove(CardType.COLORLESS)
        assert attack.cost == expected
    a.attachment.remove(tool)
    refresh_costs(a, s)
    assert [a.cost for a in a.attacks] == original


def test_big_balloon_requires_stage_two():
    s, p, o, a, b = board()
    attach("大气球", s, p, a)
    original = list(a.retreat)
    refresh_costs(a, s)
    assert a.retreat == original
    a.stage = Stage.STAGE_2
    refresh_costs(a, s)
    assert a.retreat == []

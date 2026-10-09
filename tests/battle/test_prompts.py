from types import SimpleNamespace

import pytest
from packages.battle.prompts import describe


@pytest.mark.parametrize(
    "side,expected",
    [
        ("self", "选择我方要移出伤害指示物的宝可梦，随后选择移动数量"),
        ("opponent", "选择对方接收伤害指示物的宝可梦"),
    ],
)
def test_munkidori_direction(side, expected):
    game = SimpleNamespace(
        env=SimpleNamespace(phase="playing"),
        actions=SimpleNamespace(hidden=False),
        info={"prompt": SimpleNamespace(source=SimpleNamespace(name="Munkidori"))},
    )
    assert describe(game, {"candidates": [{"side": side}]}) == expected


def test_energy_payment_prompt_explains_units_and_mew_nested_choices():
    from ptcg.core.card_registry import registry

    game = SimpleNamespace(
        env=SimpleNamespace(phase="playing"),
        actions=SimpleNamespace(hidden=False, candidates=[registry.get("SVE-008")()]),
        info={
            "prompt": SimpleNamespace(
                source=SimpleNamespace(name="Mew ex"),
                tips="Choose Energy cards (1/2 units available in selected cards); stop when sufficient.",
            )
        },
    )
    assert "1 / 2 个能量" in describe(game, {})
    game.info["prompt"].tips = ""
    assert describe(game, {}) != "选择要复制的招式"

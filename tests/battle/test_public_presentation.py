"""Public Tool declarations and read-only saved-frame presentation."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from packages.battle.presentation import TOOL_SOURCE_LABEL, tool_attack_event
from packages.battle.service import MatchService, runtime


def public_attack(attack="Devolution", side="self"):
    tool = "Technical Machine: " + attack
    holder = {
        "id": "TWM-095",
        "name": "Munkidori",
        "tool": [tool],
        "attacks": [{"name": "Mind Bend"}],
    }
    observation = {side: {"active": [holder]}}
    event = {
        "text": "攻击 · 愿增猿 · " + attack,
        "actionType": "AttackAction",
        "sourceLocation": side + ":active:0",
        "cardGroups": [],
    }
    return event, observation


@pytest.mark.parametrize(
    "attack,engine_id", [("Devolution", "P4T-DDB79F5FA206"), ("Evolution", "PAR-178")]
)
@pytest.mark.parametrize("side", ["self", "opponent"])
def test_named_tm_from_saved_public_frame(attack, engine_id, side):
    event, before = public_attack(attack, side)
    original = deepcopy((event, before))
    shown = tool_attack_event(event, before)
    assert shown["cardGroups"] == [
        {
            "label": TOOL_SOURCE_LABEL,
            "cards": [
                {
                    "id": engine_id,
                    "name": "Technical Machine: " + attack,
                    "attacks": [{"name": attack}],
                }
            ],
        }
    ]
    assert (event, before) == original
    assert tool_attack_event(shown, before) == shown


@pytest.mark.parametrize(
    "failure",
    [
        "not_attached",
        "hand",
        "negative_index",
        "own_attack",
        "not_attack",
        "unknown_tool",
    ],
)
def test_no_tool_source_inferred_from_private_or_unrelated_cards(failure):
    event, before = public_attack()
    holder = before["self"]["active"][0]
    if failure == "not_attached":
        holder["tool"] = []
    elif failure == "hand":
        event["sourceLocation"] = "self:hand:0"
        before["self"]["hand"] = [holder]
    elif failure == "negative_index":
        event["sourceLocation"] = "self:active:-1"
    elif failure == "own_attack":
        holder["attacks"] = [{"name": "Devolution"}]
    elif failure == "not_attack":
        event["actionType"] = "UseAbilityAction"
    else:
        holder["tool"] = ["Technical Machine: Unknown"]
        event["text"] = "攻击 · 愿增猿 · Unknown"
    assert tool_attack_event(event, before) == event


def test_runtime_exports_only_attached_public_tool_metadata():
    rt = runtime()
    tool = SimpleNamespace(name="Technical Machine: Evolution", trainerType="TOOL")
    holder = SimpleNamespace(name="Munkidori", id="TWM-095", attachment=[tool])
    other = SimpleNamespace(name="Private effect", trainerType="TOOL")
    players = [
        SimpleNamespace(
            id=seat,
            active=[holder] if seat == rt.PlayerId.PLAYER1 else [],
            bench=[],
            hand=[],
            discard=[],
        )
        for seat in rt.PlayerId
    ]
    observation = {
        side: {
            zone: [{}] if side == "self" and zone == "active" else []
            for zone in ("active", "bench", "hand", "discard")
        }
        for side in ("self", "opponent")
    }
    game = SimpleNamespace(
        version=1,
        info={},
        actions=[SimpleNamespace(source=holder, effect_source=tool)],
        env=SimpleNamespace(
            get_players=lambda: players, gamestate=SimpleNamespace(stadium=[])
        ),
        view=lambda viewer: {
            "observation": deepcopy(observation),
            "decision": {
                "kind": "options",
                "options": [{"actionType": "AttackAction"}],
            },
        },
        _card=lambda card: {
            "id": "PAR-178",
            "name": card.name,
            "attacks": [{"name": "Evolution"}],
            "private": "never-export",
        },
    )
    view = rt.view(game, rt.PlayerId.PLAYER1)
    source = view["decision"]["options"][0]["effectSource"]
    assert source == {
        "id": "PAR-178",
        "name": tool.name,
        "attacks": [{"name": "Evolution"}],
    }
    game.actions[0].effect_source = other
    assert (
        "effectSource"
        not in rt.view(game, rt.PlayerId.PLAYER1)["decision"]["options"][0]
    )


def test_exact_effect_source_is_in_action_event_without_private_candidates():
    from tests.protocol.test_projection import original

    view = original()
    holder = {"id": "TWM-095", "name": "Munkidori", "ref": "v0:self:active:0"}
    view["observation"]["self"]["active"] = [holder]
    tool = {
        "id": "PAR-178",
        "name": "Technical Machine: Evolution",
        "attacks": [{"name": "Evolution"}],
    }
    view["decision"]["options"] = [
        {
            "id": "attack",
            "actionType": "AttackAction",
            "sourceRef": holder["ref"],
            "source": holder["name"],
            "attack": {"name": "Evolution", "damage": 0},
            "effectSource": tool,
        }
    ]
    event = MatchService._event(view, {"choice": {"optionId": "attack"}})
    assert event["cardGroups"][-1] == {"label": TOOL_SOURCE_LABEL, "cards": [tool]}
    assert event["text"].endswith(" · Evolution")

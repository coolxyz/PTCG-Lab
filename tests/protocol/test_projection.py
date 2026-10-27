"""Original synthetic rich projections; no upstream/card data."""

from copy import deepcopy

import pytest
from ptcg_protocol.projection import snapshot, legacy_view, replay_page, core_command
from ptcg_protocol.validation import validate, ProtocolViolation


@pytest.mark.parametrize("identity", [{}, {"id": None}, {"id": ""}])
def test_missing_card_id_keeps_printing_identity_in_projection(identity):
    from ptcg_protocol.projection import card_identity

    card = dict(
        name="Synthetic card with spaces", set_name="SYN", number="042", **identity
    )
    assert card_identity(card) == "SYN-042"
    assert card_identity({**card, "id": "EXPLICIT-001"}) == "EXPLICIT-001"


def original():
    card = {
        "id": "SYN-001",
        "name": "Original test card",
        "kind": "card",
        "ref": "v0:self:active:0",
        "hp": -10,
        "maximumHp": 160,
        "attacks": [{"name": "Test", "damage": 20, "cost": ["FIRE"]}],
        "abilityUsed": True,
        "abilitiesSuppressed": True,
        "specialCondition": "CONFUSED",
        "poisoned": True,
        "poisonDamage": 30,
        "burned": True,
        "retreatBlocked": True,
        "attackBlocked": True,
        "limitedAttacks": ["Test"],
        "attackProtection": {"damage": True, "effects": False, "source": "test"},
        "recovery": {"amount": 20, "cure": True},
        "attackDamageReduction": 20,
        "preventAttackDamageAtMost": 30,
        "attackPowerReduction": 10,
        "attackPowerBonus": 20,
        "retaliationCounters": 2,
        "damage_counters": 170,
        "energy": ["FIRE"],
        "attachment": ["Original test card"],
        "evolved": [],
        "tool": [],
    }
    side = {
        "id": "player1",
        "active": [card],
        "bench": [],
        "discard": [],
        "lost_zone": [],
        "hand": [],
        "hand_count": 0,
        "deck_count": 50,
        "prize_count": 6,
        "faceUpPrizes": [],
        "benchCapacity": 5,
        "turnFlags": dict(
            firstPersonalTurn=False,
            energyPlayed=True,
            supporterPlayed=False,
            itemsBlocked=False,
            retreated=False,
        ),
    }
    other = {**deepcopy(side), "id": "player2", "active": [], "hand": None}
    return dict(
        stateVersion=0,
        done=False,
        phase="playing",
        winner=None,
        startingPlayer="PLAYER1",
        catalogVersion="synthetic",
        setupEvents=[{"kind": "order_draw", "winner": "PLAYER1"}],
        publicReveals=[],
        observation=dict(
            viewer="player1",
            turn="player1",
            turn_number=1,
            timestep=2,
            is_choosing_card=False,
            termination_reason=None,
            auto_events=[],
            self=side,
            opponent=other,
            stadium=[],
        ),
        decision=dict(
            id="d0",
            actor="PLAYER1",
            kind="options",
            options=[
                dict(
                    id="d0:o0",
                    actionType="AttackAction",
                    source="Original test card",
                    sourceId="SYN-001",
                    sourceRef="v0:self:active:0",
                    targetRef=None,
                    attack={"name": "Test", "damage": 20},
                )
            ],
        ),
    )


def project(v=None, **kw):
    return snapshot(
        original() if v is None else v,
        match_id="m",
        rule_release_id="rules-test",
        viewer_seat="PLAYER1",
        match_seq=kw.get("seq", 0),
        controller_epoch=1,
        stream_cursor="s0",
    )


def test_all_visible_effects_round_trip_and_detached():
    v = original()
    wire = project(v)
    assert legacy_view(wire) == v
    wire["observation"]["self"]["active"][0]["card"]["presentation"]["hp"] = 99
    assert v["observation"]["self"]["active"][0]["hp"] == -10


@pytest.mark.parametrize("where", ["root", "observation", "side", "card", "option"])
def test_unknown_visible_fields_fail_closed(where):
    v = original()
    target = {
        "root": v,
        "observation": v["observation"],
        "side": v["observation"]["self"],
        "card": v["observation"]["self"]["active"][0],
        "option": v["decision"]["options"][0],
    }[where]
    target["recoveryRecord"] = "private-sentinel"
    with pytest.raises(ProtocolViolation):
        project(v)


def prize_view():
    v = original()
    card = {"id": "SYN-002", "name": "Public prize", "kind": "card"}
    v["observation"]["self"]["faceUpPrizes"] = [dict(card, prizeIndex=2)]
    v["decision"] = dict(
        id="d0",
        actor="PLAYER1",
        kind="selection",
        min=1,
        max=1,
        hidden=True,
        indexed=False,
        source=None,
        sourceId=None,
        zones=[],
        instruction="Choose",
        candidates=[
            {"ref": "d0:c0"},
            {
                "ref": "d0:c1",
                "card": card,
                "revealedPrizeIndex": 2,
                "revealedPrizeSide": "self",
            },
        ],
    )
    return v


def test_public_prize_binding_is_preserved_in_hidden_choice():
    v = prize_view()
    wire = project(v)
    assert legacy_view(wire) == v
    assert wire["decision"]["candidates"][0] == {"ref": "d0:c0"}


@pytest.mark.parametrize(
    "change", ["index", "side", "content", "unmarked", "hidden-field"]
)
def test_hidden_choice_cannot_claim_unknown_reveal(change):
    v = prize_view()
    c = v["decision"]["candidates"][1]
    if change == "index":
        c["revealedPrizeIndex"] = 3
    elif change == "side":
        c["revealedPrizeSide"] = "opponent"
    elif change == "content":
        c["card"]["name"] = "secret card"
    elif change == "unmarked":
        c.pop("revealedPrizeIndex")
    else:
        v["decision"]["candidates"][0]["position"] = "CardPosition.PRIZE"
    with pytest.raises(ProtocolViolation):
        project(v)


def test_private_replay_is_unrepresentable_and_mixed_seats_rejected():
    wire = project()
    page = replay_page([wire])
    assert not set(page["frames"][0]) & {"decision", "controllerEpoch", "streamCursor"}
    assert page["frames"][0]["observation"]["opponent"]["hand"] is None
    page["frames"][0]["recoveryRecord"] = {}
    with pytest.raises(ProtocolViolation):
        validate("ReplayPage", page)
    other = deepcopy(wire)
    other["viewerSeat"] = "PLAYER2"
    other["decision"] = None
    with pytest.raises(ProtocolViolation):
        replay_page([wire, other])


def test_non_turn_actor_uses_decision_not_turn():
    v = original()
    v["observation"]["turn"] = "player2"
    wire = project(v)
    command = dict(
        protocolVersion="2.0",
        commandId="c",
        expectedMatchSeq=0,
        expectedStateVersion=0,
        controllerEpoch=1,
        decisionId="d0",
        choice={"optionId": "d0:o0"},
    )
    mapped = core_command(command, trusted_view=wire, authenticated_seat="PLAYER1")
    assert mapped == {
        k: command[k]
        for k in ("commandId", "expectedStateVersion", "decisionId", "choice")
    }
    with pytest.raises(ProtocolViolation):
        core_command(command, trusted_view=wire, authenticated_seat="PLAYER2")


def test_terminal_view_keeps_opponent_hidden_and_replay_read_only():
    v = original()
    v.update(done=True, winner="PLAYER1", decision=None)
    wire = project(v)
    page = replay_page([wire])
    assert wire["status"] == "finished"
    assert page["frames"][0]["observation"]["opponent"]["hand"] is None

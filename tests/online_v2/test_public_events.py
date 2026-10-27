"""Announcements disclose the accepted public action, never a private search."""

from copy import deepcopy
import json
import pytest
from ptcg_online_server.public_events import action_events, coin_results
from ptcg_protocol.validation import validate
from ptcg_protocol.projection import snapshot
from tests.protocol.test_projection import original


def validate_event(event, seq):
    frame = snapshot(
        original(),
        match_id="synthetic",
        rule_release_id="rules",
        viewer_seat="PLAYER1",
        match_seq=seq,
        controller_epoch=0,
        stream_cursor="cursor",
    )
    frame["events"] = [{**event, "seq": seq}]
    validate("MatchSnapshot", frame)


def action(kind="UseItemAction", phase="playing"):
    view = original()
    view["phase"] = phase
    played = {
        "id": "SYN-PLAY",
        "name": "Publicly played",
        "ref": "v0:self:hand:0",
        "privateDiagnostic": "never-export",
    }
    view["observation"]["self"]["hand"] = [
        played,
        {"name": "unplayed-secret", "ref": "v0:self:hand:1"},
    ]
    view["decision"]["options"] = [
        {
            "id": "option",
            "actionType": kind,
            "source": played["name"],
            "sourceRef": played["ref"],
            "targetRef": "v0:self:hand:1",
            "label": "secret-deck-order",
            "chosen": ["unplayed-secret"],
        }
    ]
    return view


@pytest.mark.parametrize(
    "kind",
    [
        "PlayPokemonAction",
        "EvolvePokemonAction",
        "AttachEnergyAction",
        "UseSupporterAction",
        "UseItemAction",
        "UseToolAction",
        "PutStadiumAction",
    ],
)
def test_only_played_hand_card_is_announced(kind):
    view = action(kind)
    original_view = deepcopy(view)
    event = action_events(view, "PLAYER1", {"optionId": "option"})[0]
    assert event["cards"][0]["name"] == "Publicly played"
    assert len(event["cards"]) == 1
    text = json.dumps(event)
    assert all(
        s not in text
        for s in (
            "unplayed-secret",
            "secret-deck-order",
            "never-export",
            "self:hand",
            "sourceRef",
        )
    )
    validate_event(event, 3)
    assert view == original_view


@pytest.mark.parametrize("phase", ["active", "bench", "choose_order", "mulligan"])
def test_setup_is_generic_until_rules_explicitly_reveal_cards(phase):
    event = action_events(action(phase=phase), "PLAYER1", {"optionId": "option"})[0]
    assert event["kind"] == "setup"
    assert "cards" not in event and "Publicly played" not in event["text"]


def test_hidden_search_choices_unknown_actions_and_wrong_seats_are_not_exposed():
    view = action()
    view["decision"] = {
        "actor": "PLAYER1",
        "kind": "selection",
        "hidden": False,
        "source": "secret-source",
        "candidates": [{"ref": "x", "card": {"name": "secret-hit"}}],
    }
    event = action_events(view, "PLAYER1", {"selectedRefs": ["x"]})[0]
    assert event == {"actor": "PLAYER1", "kind": "action", "text": "完成一次效果选择"}
    assert not action_events(view, "PLAYER2", {"selectedRefs": ["x"]})
    assert (
        "cards"
        not in action_events(
            action("PrivateOrderingAction"), "PLAYER1", {"optionId": "option"}
        )[0]
    )
    assert (
        "cards"
        not in action_events(
            action("UseAbilityAction"), "PLAYER1", {"optionId": "option"}
        )[0]
    )


def test_public_attack_names_and_exact_coin_outcomes_are_preserved():
    view = original()
    event = action_events(
        view,
        "PLAYER1",
        {"optionId": "d0:o0"},
        coin_results(
            {
                "auto_executed": [
                    "Coin flip: HEADS.",
                    "private-card",
                    "Coin flip: TAILS.",
                    {"secret": "no"},
                ]
            }
        ),
    )[0]
    assert event["text"] == "使用招式 · Original test card · Test"
    assert event["coins"] == ["heads", "tails"]
    assert event["cards"][0]["presentation"]["attacks"][0]["name"] == "Test"
    validate_event(event, 8)


@pytest.mark.parametrize(
    "kind,verb",
    [
        ("UseStadiumAction", "使用竞技场效果"),
        ("DiscardStadiumAction", "移除竞技场"),
        ("UseAbilityAction", "使用特性"),
        ("RetreatAction", "撤退"),
        ("PassTurn", "结束回合"),
    ],
)
def test_public_actions_are_named_even_without_a_board_delta(kind, verb):
    view = original()
    source = {"id": "SYN-SOURCE", "name": "Public source", "ref": "v0:stadium:0"}
    if kind in ("UseStadiumAction", "DiscardStadiumAction"):
        view["observation"]["stadium"] = [source]
    else:
        source["ref"] = "v0:self:active:0"
        view["observation"]["self"]["active"] = [source]
    option = {"id": "named", "actionType": kind}
    if kind != "PassTurn":
        option["active_pokemonRef" if kind == "RetreatAction" else "sourceRef"] = (
            source["ref"]
        )
    if kind == "UseAbilityAction":
        option["ability"] = "Public ability"
    view["decision"]["options"] = [option]
    event = action_events(view, "PLAYER1", {"optionId": "named"})[0]
    assert event["text"].startswith(verb)
    assert ("Public source" in event["text"]) == (kind != "PassTurn")
    if kind == "UseAbilityAction":
        assert "Public ability" in event["text"]
    validate_event(event, 1)


def test_selection_context_uses_exact_public_object_not_hidden_duplicate():
    from types import SimpleNamespace as NS
    from ptcg_online_server.public_events import selection_source_ref

    view = original()
    view["decision"] = {
        "actor": "PLAYER1",
        "kind": "selection",
        "hidden": False,
        "candidates": [{"ref": "private", "card": {"name": "private-hit"}}],
    }
    source = NS(name="Public stadium", id="SYN-STADIUM")
    view["observation"]["stadium"] = [
        {"id": source.id, "name": source.name, "ref": "v0:stadium:0"}
    ]
    players = [
        NS(id=NS(name=seat), active=[], bench=[], discard=[])
        for seat in ("PLAYER1", "PLAYER2")
    ]
    adapter = NS(
        info={"prompt": NS(source=source)},
        env=NS(get_players=lambda: players, gamestate=NS(stadium=[source])),
    )
    ref = selection_source_ref(adapter, view)
    assert ref == "v0:stadium:0"
    event = action_events(
        view, "PLAYER1", {"selectedRefs": ["private"]}, selection_source=ref
    )[0]
    assert event["text"] == "竞技场效果 · Public stadium · 完成选牌"
    assert "private-hit" not in json.dumps(event)
    assert "selectedRefs" not in json.dumps(event)
    validate_event(event, 2)
    # Same printed identity can exist in a private zone. Equality is insufficient.
    adapter.info["prompt"].source = NS(name=source.name, id=source.id)
    assert selection_source_ref(adapter, view) is None
    assert (
        "cards"
        not in action_events(view, "PLAYER1", {}, selection_source="v0:self:hand:0")[0]
    )
    view["phase"] = "bench"
    adapter.info["prompt"].source = source
    assert selection_source_ref(adapter, view) is None


@pytest.mark.parametrize("zone", ["active", "bench", "discard"])
def test_selection_from_a_public_source_keeps_only_printed_identity(zone):
    from types import SimpleNamespace as NS
    from ptcg_online_server.public_events import selection_source_ref

    view = original()
    view["decision"] = {
        "actor": "PLAYER1",
        "kind": "selection",
        "hidden": True,
        "candidates": [],
    }
    source = NS()
    player = NS(id=NS(name="PLAYER1"), active=[], bench=[], discard=[])
    getattr(player, zone).append(source)
    view["observation"]["self"][zone] = [
        {
            "id": "SYN-PUBLIC",
            "name": "Public card",
            "ref": "v0:self:" + zone + ":0",
            "privateDiagnostic": "never-copy",
        }
    ]
    adapter = NS(
        info={"prompt": NS(source=source)},
        env=NS(get_players=lambda: [player], gamestate=NS(stadium=[])),
    )
    event = action_events(
        view, "PLAYER1", {}, selection_source=selection_source_ref(adapter, view)
    )[0]
    assert event["text"] == "卡牌效果 · Public card · 完成选牌"
    assert "never-copy" not in json.dumps(event)
    validate_event(event, 3)


def test_numeric_effect_options_do_not_publish_arbitrary_labels_or_values():
    view = action("NumberOption")
    view["decision"]["options"][0].update(
        label="private-trigger-name", value="private-value"
    )
    event = action_events(view, "PLAYER1", {"optionId": "option"})[0]
    assert event == {"actor": "PLAYER1", "kind": "action", "text": "完成效果选项选择"}


@pytest.mark.parametrize(
    "kind",
    ["UseStadiumAction", "DiscardStadiumAction", "UseAbilityAction", "UseItemAction"],
)
def test_public_record_without_explicit_id_uses_the_board_printing_identity(kind):
    view = original()
    card = dict(
        name="Synthetic public card with spaces",
        set_name="SYN",
        number="042",
        ref="v0:stadium:0",
        privateDiagnostic="never-export",
    )
    zone = (
        view["observation"]["stadium"]
        if "Stadium" in kind
        else view["observation"]["self"]["active"]
    )
    zone[:] = [card]
    view["decision"]["options"] = [
        dict(
            id="use",
            actionType=kind,
            sourceRef=card["ref"],
            active_pokemonRef=card["ref"],
        )
    ]
    event = action_events(view, "PLAYER1", {"optionId": "use"})[0]
    assert [c["cardId"] for c in event["cards"]] == ["SYN-042"]
    assert event["cards"][0]["name"] == card["name"]
    assert "never-export" not in json.dumps(event)
    # Private diagnostics are excluded from the event. They are not part of the
    # independent, schema-valid board projection used to compare its identity.
    del card["privateDiagnostic"]
    for seat in ("PLAYER1", "PLAYER2"):
        scoped = deepcopy(view)
        if seat == "PLAYER2":
            scoped["observation"]["viewer"] = "player2"
            scoped["observation"]["self"]["id"] = "player2"
            scoped["observation"]["opponent"]["id"] = "player1"
            scoped["decision"] = None
        frame = snapshot(
            scoped,
            match_id="synthetic",
            rule_release_id="rules",
            viewer_seat=seat,
            match_seq=1,
            controller_epoch=0,
            stream_cursor="cursor",
        )
        frame["events"] = [{**event, "seq": 1}]
        validate("MatchSnapshot", frame)


def test_selection_and_same_name_printings_keep_distinct_valid_ids():
    view = original()
    source = dict(
        name="Same synthetic name", set_name="SYN", number="001", ref="v0:stadium:0"
    )
    target = {**source, "number": "002", "ref": "v0:self:active:0"}
    view["observation"]["stadium"] = [source]
    view["observation"]["self"]["active"] = [target]
    view["decision"]["options"] = [
        dict(
            id="use",
            actionType="UseStadiumAction",
            sourceRef=source["ref"],
            targetRef=target["ref"],
        )
    ]
    event = action_events(view, "PLAYER1", {"optionId": "use"})[0]
    assert [c["cardId"] for c in event["cards"]] == ["SYN-001", "SYN-002"]
    validate_event(event, 1)
    view["decision"] = dict(kind="selection", actor="PLAYER1")
    event = action_events(view, "PLAYER1", {}, selection_source=source["ref"])[0]
    assert event["cards"][0]["cardId"] == "SYN-001"
    validate_event(event, 2)

"""Original synthetic contracts, independent of card assets and running services."""

import copy
from importlib.resources import files
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from ptcg_protocol.validation import (
    decode,
    validate,
    validate_command,
    ProtocolViolation,
)

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests/synthetic/protocol/v2.json").read_text())


def example(index):
    return copy.deepcopy(CASES[index]["value"])


def reject(kind, value, code="INVALID_PAYLOAD"):
    with pytest.raises(ProtocolViolation) as error:
        validate(kind, value)
    assert str(error.value) == code


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["kind"])
def test_synthetic_wire_examples(case):
    assert validate(case["kind"], case["value"]) == case["value"]
    assert validate(case["kind"], case["value"]) is not case["value"]


def test_schema_is_closed_and_has_only_local_references():
    schema = json.loads(
        files("ptcg_protocol").joinpath("schema/v2.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            if "$ref" in node:
                assert node["$ref"].startswith("#/$defs/")
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


@pytest.mark.parametrize(
    "key",
    ["seed", "stateDigest", "recoveryRecord", "rawInfo", "seat", "token", "damage"],
)
def test_unknown_command_fields_are_never_forwarded(key):
    command = example(0)
    command[key] = "private-sentinel-do-not-echo"
    reject("Command", command)


@pytest.mark.parametrize(
    "choice",
    [
        {},
        {"optionId": "d2:o0", "selectedRefs": []},
        {"selectedRefs": ["d2:c0", "d2:c0"]},
        {"selectedRefs": ["x"] * 61},
        {"optionId": ""},
    ],
)
def test_choice_is_exactly_one_bounded_form(choice):
    command = example(0)
    command["choice"] = choice
    reject("Command", command)


@pytest.mark.parametrize("value", [-1, True, 1.0, "1", 9007199254740992])
def test_sequences_are_exact_safe_integers(value):
    command = example(0)
    command["expectedMatchSeq"] = value
    reject("Command", command)


@pytest.mark.parametrize("version", ["1.0", "2.1", "3.0", 2, None])
def test_unsupported_versions_fail_closed(version):
    command = example(0)
    command["protocolVersion"] = version
    reject("Command", command, "UNSUPPORTED_PROTOCOL")


@pytest.mark.parametrize(
    "payload",
    [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"\xff", b"{", b"null"],
)
def test_invalid_json_is_redacted(payload):
    with pytest.raises(ProtocolViolation) as error:
        decode("Command", payload)
    assert str(error.value) in {"INVALID_JSON", "INVALID_PAYLOAD"}


def test_byte_and_depth_limits():
    with pytest.raises(ProtocolViolation, match="PAYLOAD_TOO_LARGE"):
        decode("Command", b" " * 16385)
    with pytest.raises(ProtocolViolation, match="PAYLOAD_TOO_LARGE"):
        decode("Command", b"[" * 40 + b"0" + b"]" * 40)
    command = example(0)
    command["commandId"] = "a" * 129
    reject("Command", command)


def test_local_non_json_objects_and_cycles_fail_cleanly():
    reject("Command", {"x": object()}, "INVALID_JSON")
    cycle = []
    cycle.append(cycle)
    reject("Command", cycle, "INVALID_JSON")


@pytest.mark.parametrize("index", [2, 3])
def test_both_views_reject_opponent_hand_and_private_zones(index):
    value = example(index)
    value["observation"]["opponent"]["hand"] = [{"cardId": "secret", "name": "hidden"}]
    reject("MatchSnapshot", value)
    for zone in ("deck", "prizes", "seed", "rng"):
        value = example(index)
        value["observation"]["self"][zone] = ["private-sentinel"]
        reject("MatchSnapshot", value)


def test_hidden_candidates_have_no_identity_or_position():
    for key, value in [
        ("card", {"cardId": "hidden", "name": "hidden"}),
        ("boardRef", "hidden"),
        ("index", 0),
    ]:
        snapshot = example(4)
        snapshot["decision"]["candidates"][0][key] = value
        reject("MatchSnapshot", snapshot)


def test_foreign_decision_or_finished_decision_rejected():
    snapshot = example(2)
    snapshot["viewerSeat"] = "PLAYER2"
    reject("MatchSnapshot", snapshot)
    snapshot = example(2)
    snapshot.update(done=True, status="finished", winner="PLAYER1")
    reject("MatchSnapshot", snapshot)


def test_hidden_and_unordered_search_contract():
    inspection = {
        "owner": "self",
        "ordered": False,
        "total": 1,
        "cards": [{"card": {"cardId": "synthetic-1", "name": "合成卡"}, "count": 1}],
    }
    snapshot = example(4)
    snapshot["decision"]["deckInspection"] = inspection
    reject("MatchSnapshot", snapshot)
    snapshot["decision"]["hidden"] = False
    validate("MatchSnapshot", snapshot)
    inspection["ordered"] = True
    reject("MatchSnapshot", snapshot)


def test_duplicate_candidates_and_bounds():
    snapshot = example(4)
    snapshot["decision"]["candidates"][1] = snapshot["decision"]["candidates"][0]
    reject("MatchSnapshot", snapshot)
    snapshot = example(4)
    snapshot["decision"]["min"] = 3
    reject("MatchSnapshot", snapshot)


def test_commands_use_current_actor_choices_and_authenticated_seat():
    for cmd_index, view_index in [(0, 2), (1, 4)]:
        command = example(cmd_index)
        assert (
            validate_command(
                command, trusted_view=example(view_index), authenticated_seat="PLAYER1"
            )
            == command
        )
    with pytest.raises(ProtocolViolation, match="FORBIDDEN"):
        validate_command(
            example(0), trusted_view=example(2), authenticated_seat="PLAYER2"
        )
    with pytest.raises(ProtocolViolation, match="NOT_ACTOR"):
        validate_command(
            example(0), trusted_view=example(3), authenticated_seat="PLAYER2"
        )


@pytest.mark.parametrize(
    "key,value,code",
    [
        ("controllerEpoch", 0, "CONTROL_LOST"),
        ("expectedMatchSeq", 2, "MATCH_VERSION_CONFLICT"),
        ("expectedStateVersion", 0, "STALE_DECISION"),
        ("decisionId", "d1", "STALE_DECISION"),
        ("choice", {"optionId": "d2:o999"}, "INVALID_OPTION"),
        ("choice", {"selectedRefs": []}, "INVALID_OPTION"),
    ],
)
def test_stale_and_invalid_option_commands(key, value, code):
    command = example(0)
    command[key] = value
    with pytest.raises(ProtocolViolation, match=code):
        validate_command(command, trusted_view=example(2), authenticated_seat="PLAYER1")


@pytest.mark.parametrize(
    "choice", [{"selectedRefs": []}, {"selectedRefs": ["d1:c0"]}, {"optionId": "d2:o0"}]
)
def test_invalid_selection_commands(choice):
    command = example(1)
    command["choice"] = choice
    with pytest.raises(ProtocolViolation, match="INVALID_SELECTION"):
        validate_command(command, trusted_view=example(4), authenticated_seat="PLAYER1")


@pytest.mark.parametrize(
    "field", ["decision", "controllerEpoch", "seed", "commands", "recoveryRecord"]
)
def test_replay_is_never_executable_or_restorable(field):
    page = example(8)
    page["frames"][0][field] = "private-sentinel"
    reject("ReplayPage", page)


def test_replay_order_and_bounds():
    page = example(8)
    page["frames"] *= 2
    reject("ReplayPage", page)
    page["frames"] *= 17
    reject("ReplayPage", page)


def test_deck_does_not_accept_metadata_and_checks_total():
    for key in ("image", "notes", "owned", "engineLine"):
        deck = example(5)
        deck["entries"][0][key] = "private-sentinel"
        reject("DeckSubmission", deck)
    deck = example(5)
    deck["entries"][0]["quantity"] = 59
    reject("DeckSubmission", deck)


def test_import_is_inert_and_has_no_product_dependency(tmp_path):
    # Runs against the installed wheel, outside the checkout and PYTHONPATH.
    code = """
import sys
import ptcg_protocol
assert "jsonschema" not in sys.modules
from ptcg_protocol import v2
from ptcg_protocol.validation import validate
assert not any(n.split(".")[0] in {"packages", "apps", "ptcg", "fastapi", "sqlite3", "torch"} for n in sys.modules)
validate("ProtocolError", {"protocolVersion":"2.0","code":"INVALID_PAYLOAD","retryable":False})
"""
    subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, check=True)

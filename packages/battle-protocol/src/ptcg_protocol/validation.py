"""Bounded v2 decoding; no network, engine, database, or import-time asset reads.

Authentication, membership, durable idempotency and rule legality belong to the
caller. This module checks wire shape and choice membership in a trusted view.
"""

from functools import lru_cache
from importlib.resources import files
import json

from jsonschema import Draft202012Validator, validators
from referencing import Registry

PROTOCOL_VERSION = "2.0"
LIMITS = {
    "Command": 16_384,
    "DeckSubmission": 16_384,
    "MatchSnapshot": 524_288,
    "Capabilities": 16_384,
    "ProtocolError": 4_096,
    "ReplayPage": 2_097_152,
}


class ProtocolViolation(ValueError):
    """Only a stable code is exposed, never validator input or hidden values."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code="INVALID_PAYLOAD"):
    raise ProtocolViolation(code)


@lru_cache(maxsize=6)
def _validator(kind):
    if kind not in LIMITS:
        raise ValueError("UNKNOWN_MESSAGE_KIND")
    schema = json.loads(
        files("ptcg_protocol").joinpath("schema/v2.schema.json").read_text()
    )
    # Only packaged local references are allowed. The explicit empty registry
    # has no HTTP retriever; validation can never fetch a schema from the wire.
    integer = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int
    )
    cls = validators.extend(Draft202012Validator, type_checker=integer)
    selected = {"$defs": schema["$defs"], "$ref": "#/$defs/" + kind}
    cls.check_schema(schema)
    return cls(selected, registry=Registry())


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("INVALID_JSON")
        result[key] = value
    return result


def _constant(value):
    _fail("INVALID_JSON")


def _bounded_tree(value):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > 32 or nodes > 100_000:
            _fail("PAYLOAD_TOO_LARGE")
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)


def decode(kind, payload):
    """Decode UTF-8 bytes using a fixed message kind selected by the endpoint."""
    if kind not in LIMITS:
        raise ValueError("UNKNOWN_MESSAGE_KIND")
    if not isinstance(payload, bytes):
        _fail("INVALID_JSON")
    if len(payload) > LIMITS[kind]:
        _fail("PAYLOAD_TOO_LARGE")
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ProtocolViolation):
            raise
        raise ProtocolViolation("INVALID_JSON") from None
    _bounded_tree(value)
    if isinstance(value, dict) and "protocolVersion" in value:
        if value["protocolVersion"] != PROTOCOL_VERSION:
            _fail("UNSUPPORTED_PROTOCOL")
    # Do not stringify ValidationError: its message includes rejected values.
    if next(_validator(kind).iter_errors(value), None) is not None:
        _fail()
    _semantics(kind, value)
    return value


def validate(kind, value):
    """Validate JSON-compatible in-process values and return a detached copy."""
    try:
        raw = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProtocolViolation("INVALID_JSON") from None
    return decode(kind, raw)


def _unique(rows, field):
    if len({r[field] for r in rows}) != len(rows):
        _fail()


def _frame(frame):
    own = frame["observation"]["self"]
    if own["handCount"] != len(own["hand"]):
        _fail()
    if frame["done"] != (frame["status"] != "playing"):
        _fail()
    if frame["winner"] is not None and frame["status"] != "finished":
        _fail()
    refs = []
    for side in ("self", "opponent"):
        current = frame["observation"][side]
        refs += [c["ref"] for zone in ("active", "bench") for c in current[zone]]
        if len(current["bench"]) > current["benchCapacity"]:
            _fail()
        _unique(current["faceUpPrizes"], "index")
        if any(p["index"] >= current["prizeCount"] for p in current["faceUpPrizes"]):
            _fail()
    refs += [c["ref"] for c in frame["observation"]["stadium"]]
    if len(set(refs)) != len(refs):
        _fail()
    seqs = [e["seq"] for e in frame["events"]]
    if seqs != sorted(set(seqs)):
        _fail()


def _semantics(kind, value):
    if kind == "DeckSubmission":
        _unique(value["entries"], "printingId")
        if sum(e["quantity"] for e in value["entries"]) != 60:
            _fail()
    elif kind == "MatchSnapshot":
        _frame(value)
        decision = value["decision"]
        if decision is None:
            return
        if value["done"] or decision["actor"] != value["viewerSeat"]:
            _fail()
        if decision["kind"] == "options":
            _unique(decision["options"], "id")
        else:
            _unique(decision["candidates"], "ref")
            if (
                not 0
                <= decision["min"]
                <= decision["max"]
                <= len(decision["candidates"])
            ):
                _fail()
            if decision["hidden"]:
                if "deckInspection" in decision:
                    _fail()
                for candidate in decision["candidates"]:
                    if set(candidate) == {"ref"}:
                        continue
                    if set(candidate) != {
                        "ref",
                        "card",
                        "revealedPrizeIndex",
                        "revealedPrizeSide",
                    }:
                        _fail()
                    prizes = value["observation"][candidate["revealedPrizeSide"]][
                        "faceUpPrizes"
                    ]
                    visible = next(
                        (
                            p["card"]
                            for p in prizes
                            if p["index"] == candidate["revealedPrizeIndex"]
                        ),
                        None,
                    )
                    card = candidate["card"]
                    if (
                        visible is None
                        or visible["cardId"] != card["cardId"]
                        or visible["name"] != card["name"]
                    ):
                        _fail()
                    known = dict(visible.get("presentation", {}))
                    known.pop("prizeIndex", None)
                    if known != card.get("presentation", {}):
                        _fail()
            inspection = decision.get("deckInspection")
            if (
                inspection
                and sum(c["count"] for c in inspection["cards"]) != inspection["total"]
            ):
                _fail()
    elif kind == "ReplayPage":
        previous = None
        for frame in value["frames"]:
            _frame(frame)
            if previous is not None and (
                frame["frameSeq"] <= previous["frameSeq"]
                or frame["matchSeq"] < previous["matchSeq"]
                or frame["stateVersion"] < previous["stateVersion"]
            ):
                _fail()
            previous = frame
    elif kind == "Capabilities":
        if value["acceptingMatches"] and not value["ruleReleaseIds"]:
            _fail()


def validate_command(command, *, trusted_view, authenticated_seat):
    """Check freshness/choice against a caller-authorized, current player view.

    Durable duplicate-command lookup must happen BEFORE this function: an
    already accepted retry legitimately references an older state/epoch.
    This is not match membership authorization or engine execution.
    """
    command = validate("Command", command)
    view = validate("MatchSnapshot", trusted_view)
    if (
        authenticated_seat not in ("PLAYER1", "PLAYER2")
        or view["viewerSeat"] != authenticated_seat
    ):
        _fail("FORBIDDEN")
    if command["controllerEpoch"] != view["controllerEpoch"]:
        _fail("CONTROL_LOST")
    if command["expectedMatchSeq"] != view["matchSeq"]:
        _fail("MATCH_VERSION_CONFLICT")
    decision = view["decision"]
    if decision is None or view["done"]:
        _fail("NOT_ACTOR")
    if (
        command["expectedStateVersion"] != view["stateVersion"]
        or command["decisionId"] != decision["id"]
    ):
        _fail("STALE_DECISION")
    choice = command["choice"]
    if decision["kind"] == "options":
        if choice.get("optionId") not in {o["id"] for o in decision["options"]}:
            _fail("INVALID_OPTION")
    else:
        refs = choice.get("selectedRefs")
        if refs is None or not decision["min"] <= len(refs) <= decision["max"]:
            _fail("INVALID_SELECTION")
        if not set(refs) <= {c["ref"] for c in decision["candidates"]}:
            _fail("INVALID_SELECTION")
    return command

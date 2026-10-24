"""Exact packaged snapshots of service envelopes; shared battle DTOs stay shared."""

from functools import lru_cache
from importlib.resources import files
import json
from jsonschema import Draft202012Validator, FormatChecker, validators
from ptcg_protocol.validation import LIMITS, validate, ProtocolViolation


class ClientError(Exception):
    def __init__(self, code, status=0, *, retryable=False):
        self.code, self.status, self.retryable = code, status, retryable
        super().__init__(code)


def unpack(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def constant(_):
        raise ValueError()

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        stack = [(result, 0)]
        count = 0
        while stack:
            value, depth = stack.pop()
            count += 1
            if depth > 32 or count > 100000:
                raise ValueError()
            if isinstance(value, dict):
                stack.extend((v, depth + 1) for v in value.values())
            elif isinstance(value, list):
                stack.extend((v, depth + 1) for v in value)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ClientError("INVALID_RESPONSE") from None


@lru_cache(maxsize=24)
def validator(kind):
    filename = (
        "match"
        if kind.startswith("Match")
        else "lobby" if kind.startswith("Room") or kind == "DeckAccepted" else "session"
    )
    schema = json.loads(
        files("ptcg_online_client")
        .joinpath(f"schemas/{filename}.schema.json")
        .read_text()
    )
    integer = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _, v: type(v) is int
    )
    cls = validators.extend(Draft202012Validator, type_checker=integer)
    return cls(
        {"$defs": schema["$defs"], "$ref": "#/$defs/" + kind},
        format_checker=FormatChecker(),
    )


def checked(kind, value):
    try:
        if kind in LIMITS:
            if len(json.dumps(value, allow_nan=False).encode()) > LIMITS[kind]:
                raise ValueError()
            return validate(kind, value)
        if not validator(kind).is_valid(value):
            raise ValueError()
        return value
    except (ValueError, ProtocolViolation):
        raise ClientError("INVALID_RESPONSE") from None

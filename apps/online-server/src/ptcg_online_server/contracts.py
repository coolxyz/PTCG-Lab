"""Service-owned native session envelope, packaged beside its HTTP adapter.

Battle DTOs continue to use the unchanged shared v2 protocol package.
"""

from functools import lru_cache
from importlib.resources import files
import json
from jsonschema import Draft202012Validator, FormatChecker
from .errors import ServiceError


@lru_cache(maxsize=6)
def validator(kind):
    schema = json.loads(
        files("ptcg_online_server").joinpath("schemas/session.schema.json").read_text()
    )
    if kind not in schema["$defs"]:
        raise ValueError("UNKNOWN_SESSION_CONTRACT")
    return Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": "#/$defs/" + kind},
        format_checker=FormatChecker(),
    )


def checked(kind, value):
    if not validator(kind).is_valid(value):
        raise ServiceError("INVALID_PAYLOAD", 422)
    return value


def decode(kind, body):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError()
            value[key] = item
        return value

    def constant(_):
        raise ValueError()

    try:
        value = json.loads(
            body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError("INVALID_JSON", 400) from None
    if isinstance(value, dict) and value.get("protocolVersion") != "2.0":
        raise ServiceError("UNSUPPORTED_PROTOCOL", 400)
    return checked(kind, value)

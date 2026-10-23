"""Immutable, explicit inputs shared by providers; no filesystem access."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import re
from types import MappingProxyType


class ProviderError(ValueError):
    """Stable diagnostic code, without asset contents or machine paths."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ProviderError(code)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def is_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def canonical(value):
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ProviderError("INVALID_JSON") from None


def _pairs(rows):
    value = {}
    for key, item in rows:
        require(key not in value, "DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _constant(value):
    raise ProviderError("INVALID_JSON")


def parse_json(content):
    require(isinstance(content, bytes), "INVALID_JSON")
    try:
        value = json.loads(
            content.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
    except ProviderError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("INVALID_JSON") from None
    # Reject deep graphs before recursively freezing them.
    stack = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        require(depth <= 64, "JSON_TOO_DEEP")
        if isinstance(node, float):
            require(math.isfinite(node), "INVALID_JSON")
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return value


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class JsonSnapshot:
    digest: str
    value: Mapping

    @classmethod
    def from_bytes(cls, content):
        value = parse_json(content)
        require(isinstance(value, dict), "INVALID_JSON_DOCUMENT")
        return cls(sha256(content), freeze(value))

    def copy(self):
        return thaw(self.value)

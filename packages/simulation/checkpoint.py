"""Versioned JSON graph codec for trusted, private offline checkpoints.

No pickle, eval, constructors, or input-selected imports. Only engine classes
already loaded from the pinned source tree can be restored. This is NOT a public
upload format; size limits and type checks prevent accidental malformed input.
"""

import enum
import json
import random
import sys

MAX_NODES = 100000
MAX_BYTES = 16000000


def classes():
    from ptcg.core.card_registry import registry

    registry.list_all()
    result = {}
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith(("ptcg.", "packages.rules.")) or module is None:
            continue
        for value in vars(module).values():
            if isinstance(value, type) and value.__module__ == module_name:
                result[f"{module_name}:{value.__qualname__}"] = value
    return result


def dumps(value):
    allowed = classes()
    nodes, seen = [], {}

    def encode(obj):
        if isinstance(obj, enum.Enum):
            tag = f"{type(obj).__module__}:{type(obj).__qualname__}"
            if tag not in allowed:
                raise ValueError("CHECKPOINT_TYPE")
            return {"enum": tag, "name": obj.name}
        if obj is None or type(obj) in (str, int, float, bool):
            return obj
        if id(obj) in seen:
            return {"ref": seen[id(obj)]}
        index = len(nodes)
        if index >= MAX_NODES:
            raise ValueError("CHECKPOINT_SIZE")
        seen[id(obj)] = index
        nodes.append(None)
        if type(obj) in (list, tuple, set, frozenset):
            node = {"kind": type(obj).__name__, "items": [encode(x) for x in obj]}
        elif type(obj) is dict:
            node = {
                "kind": "dict",
                "items": [[encode(k), encode(v)] for k, v in obj.items()],
            }
        elif type(obj) is random.Random:
            node = {"kind": "rng", "state": encode(obj.getstate())}
        else:
            tag = f"{type(obj).__module__}:{type(obj).__qualname__}"
            if allowed.get(tag) is not type(obj) or not hasattr(obj, "__dict__"):
                raise ValueError(f"CHECKPOINT_TYPE: {tag}")
            node = {"kind": "object", "type": tag, "fields": encode(vars(obj))}
        nodes[index] = node
        return {"ref": index}

    root = encode(value)
    payload = json.dumps(
        {"schema": "ptcg-private-graph-v1", "root": root, "nodes": nodes},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > MAX_BYTES:
        raise ValueError("CHECKPOINT_SIZE")
    return payload


def loads(payload):
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_BYTES:
        raise ValueError("CHECKPOINT_SIZE")
    try:
        document = json.loads(payload)
        if document["schema"] != "ptcg-private-graph-v1":
            raise ValueError("CHECKPOINT_SCHEMA")
        nodes = document["nodes"]
        if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
            raise ValueError("CHECKPOINT_SIZE")
        allowed = classes()
        cache, building = {}, set()

        def decode(value):
            if value is None or type(value) in (str, int, float, bool):
                return value
            if not isinstance(value, dict):
                raise ValueError("CHECKPOINT_VALUE")
            if set(value) == {"enum", "name"}:
                cls = allowed[value["enum"]]
                if not issubclass(cls, enum.Enum):
                    raise ValueError("CHECKPOINT_ENUM")
                return cls[value["name"]]
            if set(value) != {"ref"} or type(value["ref"]) is not int:
                raise ValueError("CHECKPOINT_REFERENCE")
            index = value["ref"]
            if not 0 <= index < len(nodes):
                raise ValueError("CHECKPOINT_REFERENCE")
            if index in cache:
                return cache[index]
            if index in building:
                raise ValueError("CHECKPOINT_IMMUTABLE_CYCLE")
            building.add(index)
            node = nodes[index]
            kind = node["kind"]
            if kind == "dict":
                result = cache[index] = {}
                for key, item in node["items"]:
                    result[decode(key)] = decode(item)
            elif kind in ("list", "set"):
                result = cache[index] = [] if kind == "list" else set()
                for item in node["items"]:
                    (result.append if kind == "list" else result.add)(decode(item))
            elif kind in ("tuple", "frozenset"):
                result = (tuple if kind == "tuple" else frozenset)(
                    decode(x) for x in node["items"]
                )
            elif kind == "rng":
                result = cache[index] = random.Random(0)
                result.setstate(decode(node["state"]))
            elif kind == "object":
                cls = allowed[node["type"]]
                if issubclass(cls, enum.Enum):
                    raise ValueError("CHECKPOINT_TYPE")
                result = cache[index] = object.__new__(cls)
                fields = decode(node["fields"])
                if not isinstance(fields, dict) or any(
                    not isinstance(k, str) or k.startswith("__") for k in fields
                ):
                    raise ValueError("CHECKPOINT_FIELDS")
                vars(result).update(fields)
            else:
                raise ValueError("CHECKPOINT_KIND")
            cache[index] = result
            building.remove(index)
            return result

        return decode(document["root"])
    except (KeyError, TypeError, IndexError, AttributeError, RecursionError) as exc:
        raise ValueError("CHECKPOINT_INVALID") from exc

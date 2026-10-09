"""Versioned, fail-closed effect release manifest; no engine import in catalogue API."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "data/simulation/effects.json"
RELEASE = json.loads(PATH.read_text())
VERSION = hashlib.sha256(PATH.read_bytes()).hexdigest()
EFFECTS = {row["effectKey"]: row for row in RELEASE["effects"]}


def additive_release(previous, current):
    """Only added printing aliases may continue a historical game's rules."""
    if {k: v for k, v in previous.items() if k not in ("scope", "effects")} != {
        k: v for k, v in current.items() if k not in ("scope", "effects")
    }:
        return False
    old = {e["effectKey"]: e for e in previous["effects"]}
    new = {e["effectKey"]: e for e in current["effects"]}
    if old.keys() != new.keys():
        return False
    return all(
        {k: v for k, v in row.items() if k != "printings"}
        == {k: v for k, v in new[key].items() if k != "printings"}
        and set(row["printings"]) <= set(new[key]["printings"])
        for key, row in old.items()
    )


@lru_cache(maxsize=32)
def compatible_release(version):
    if version == VERSION:
        return True
    if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version):
        return False
    path = PATH.parents[1] / "cardpool/release-history" / (version + ".json")
    if not path.is_file():
        return False
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != version:
        return False
    try:
        return additive_release(json.loads(raw), RELEASE)
    except (KeyError, TypeError, ValueError):
        return False


def support(entries, cards):
    missing = []
    for entry in entries:
        card = cards[entry["printingId"]]
        effect = EFFECTS.get(card.get("engineId"))
        if (
            not effect
            or effect["status"] != "experimental"
            or entry["printingId"] not in effect["printings"]
            or card.get("effectStatus") != "verified"
        ):
            missing.append(entry["printingId"])
    return {
        "supported": not missing,
        "missing": missing,
        "version": VERSION,
        "status": "experimental" if not missing else "unsupported",
    }

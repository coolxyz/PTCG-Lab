"""Versioned, fail-closed effect release manifest; no engine import in catalogue API."""

import hashlib
import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "data/simulation/effects.json"
RELEASE = json.loads(PATH.read_text())
VERSION = hashlib.sha256(PATH.read_bytes()).hexdigest()
EFFECTS = {row["effectKey"]: row for row in RELEASE["effects"]}


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

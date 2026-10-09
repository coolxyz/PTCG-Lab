"""Application battle scope; separate from official format legality."""

import hashlib
import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "data/cardpool/battle-scope.json"
SCOPE = json.loads(PATH.read_text(encoding="utf-8"))
VERSION = hashlib.sha256(PATH.read_bytes()).hexdigest()


def includes(card):
    return card.get("mark") in SCOPE["allowedMarks"] or (
        card.get("category") == "能量"
        and card.get("basicEnergyType") in SCOPE["basicEnergyTypes"]
    )


def check(entries, cards):
    outside = [e["printingId"] for e in entries if not includes(cards[e["printingId"]])]
    return {
        "id": SCOPE["id"],
        "version": VERSION,
        "supported": not outside,
        "outside": outside,
    }

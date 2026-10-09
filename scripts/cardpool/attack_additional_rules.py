"""Reviewed complete attack text, grouped into shared field and zone operations."""
import json
from pathlib import Path

CLAUSES = json.loads(Path(__file__).with_name("attack-additional-clauses.json").read_text(encoding="utf8"))


def compile_additional(p):
    if p.get("damageP") or (p.get("damage") and not p["damage"].isdigit()):
        return None
    for c in CLAUSES:
        if c["english"] == p.get("eeffect") and c["chinese"] == p.get("effectZHS"):
            return dict(c["rule"])
    return None

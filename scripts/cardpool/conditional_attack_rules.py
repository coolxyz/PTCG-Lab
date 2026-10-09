"""Whole clauses whose attacks resolve without damage if a condition fails."""
import json
from pathlib import Path

CLAUSES = json.loads(Path(__file__).with_name("conditional-attack-clauses.json").read_text(encoding="utf8"))


def compile_conditional(p):
    if p.get("damageP") or not p.get("damage", "").isdigit():
        return None
    return next((dict(c["rule"]) for c in CLAUSES if c["english"] == p.get("eeffect") and c["chinese"] == p.get("effectZHS")), None)

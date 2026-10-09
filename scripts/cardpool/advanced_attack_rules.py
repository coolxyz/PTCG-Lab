"""Complete bilingual clauses and printed damage signatures, fail closed."""
import json
from pathlib import Path

CLAUSES = json.loads(Path(__file__).with_name("advanced-attack-clauses.json").read_text(encoding="utf8"))


def compile_advanced(p):
    for c in CLAUSES:
        if (c["english"] == p.get("eeffect") and c["chinese"] == p.get("effectZHS")
                and (c.get("damage") or "") == (p.get("damage") or "")
                and (c.get("damageP") or "") == (p.get("damageP") or "")):
            return dict(c["rule"])
    return None

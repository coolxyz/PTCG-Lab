"""Full bilingual compound coin clauses, with explicit printed operators."""
import json
from pathlib import Path

CLAUSES = json.loads(Path(__file__).with_name("coin-branch-clauses.json").read_text(encoding="utf8"))


def compile_branch(p):
    for c in CLAUSES:
        if c["english"] != p.get("eeffect") or c["chinese"] != p.get("effectZHS"):
            continue
        r, d = c["rule"], p.get("damage", "")
        if r.get("bonus"):
            if p.get("damageP") != "加" and not d.endswith("+"):
                return None
        elif r.get("perHead"):
            if not (p.get("damageP") == "乘" or d.endswith("×")) or d.rstrip("×") != str(r["perHead"]):
                return None
        elif p.get("damageP") or (d and not d.isdigit()):
            return None
        return dict(r)
    return None

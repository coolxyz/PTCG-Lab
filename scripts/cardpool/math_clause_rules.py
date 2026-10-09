"""Additional whole-clause, bilingual damage expressions."""

import json
from pathlib import Path

CLAUSES = json.loads(
    Path(__file__).with_name("math-clauses.json").read_text(encoding="utf8")
)


def compile_math_clause(p):
    for c in CLAUSES:
        if p.get("eeffect") != c["english"] or p.get("effectZHS") != c["chinese"]:
            continue
        r = c["rule"]
        damage = p.get("damage", "").replace("＋","+").replace("−","-")
        if r["mode"] == "add":
            if p.get("damageP") != "加" and not damage.endswith("+"):
                return None
        elif r["mode"] == "subtract":
            if p.get("damageP") != "减" and not damage.endswith("-"):
                return None
        elif not (
            (p.get("damageP") == "乘" or damage.endswith("×"))
            and damage.rstrip("×") == str(r["factor"])
        ):
            return None
        return dict(r)
    return None

"""Exact bilingual staged attacks, with independently checked damage operators."""
import json
from pathlib import Path

CLAUSES=json.loads(Path(__file__).with_name("staged-attack-clauses.json").read_text(encoding="utf8"))


def compile_staged(p):
    for c in CLAUSES:
        if c["english"]!=p.get("eeffect") or c["chinese"]!=p.get("effectZHS"):continue
        r=c["rule"];d=p.get("damage","")
        if r.get("mode")=="multiply":
            if not (p.get("damageP")=="乘" or d.endswith("×")) or d.rstrip("×")!=str(r["factor"]):return None
        elif r.get("bonus") or r.get("mode")=="add":
            if p.get("damageP")!="加" and not d.endswith("+"):return None
        elif p.get("damageP") or (d and not d.isdigit()):return None
        return dict(r)
    return None

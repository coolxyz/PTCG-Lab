"""Bilingual contracts for effects triggered by actual attack damage."""

import json
from pathlib import Path

CLAUSES = json.loads(
    Path(__file__).with_name("reactive-clauses.json").read_text(encoding="utf8")
)


def compile_reactive(en, cn):
    for clause in CLAUSES:
        if en == clause["english"] and cn == clause["chinese"]:
            return {"trigger": "passive", "usageLimit": "unlimited", **clause["rule"]}
    return None

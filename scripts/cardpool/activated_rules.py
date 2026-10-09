"""Full bilingual activated clauses with conditions and costs kept explicit."""

import json
from pathlib import Path

CLAUSES = json.loads(
    Path(__file__).with_name("activated-clauses.json").read_text(encoding="utf8")
)


def compile_activated(en, cn):
    for clause in CLAUSES:
        if en == clause["english"] and cn == clause["chinese"]:
            return {"kind": "activated_effect", **clause["rule"]}
    return None

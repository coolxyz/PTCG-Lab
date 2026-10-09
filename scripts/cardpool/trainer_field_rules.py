"""Whole bilingual clauses for shared field operations."""

import json
from pathlib import Path

CLAUSES = json.loads(
    Path(__file__).with_name("trainer-field-clauses.json").read_text(encoding="utf8")
)


def compile_field(en, cn):
    for clause in CLAUSES:
        if en == clause["english"] and cn == clause["chinese"]:
            return dict(clause["rule"])
    return None

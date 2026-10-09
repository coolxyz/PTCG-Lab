"""Complete bilingual clauses for stateful attack effects."""

import json
from pathlib import Path

CLAUSES = json.loads(
    Path(__file__).with_name("attack-field-clauses.json").read_text(encoding="utf8")
)


def compile_field(p):
    if p.get("damageP"):
        return None
    for clause in CLAUSES:
        if (
            p.get("eeffect", "") == clause["english"]
            and p.get("effectZHS", "") == clause["chinese"]
        ):
            return dict(clause["rule"])
    return None

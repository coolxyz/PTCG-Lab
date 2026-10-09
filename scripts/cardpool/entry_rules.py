"""Entry triggers match both full language clauses, never a card name alone."""

import json
from pathlib import Path

CLAUSES = json.loads(
    Path(__file__).with_name("entry-clauses.json").read_text(encoding="utf8")
)


def compile_entry(en, cn):
    for clause in CLAUSES:
        if en == clause["english"] and cn == clause["chinese"]:
            return {
                "kind": "on_entry",
                "trigger": clause["trigger"],
                "usageLimit": "once-per-hand-entry",
                "effect": clause["effect"],
                **({"requiresTrait": clause["requiresTrait"]} if clause.get("requiresTrait") else {}),
            }
    return None

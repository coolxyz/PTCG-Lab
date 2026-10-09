"""Exact bilingual contracts for shared Stadium actions and continuous rules."""

import json
from pathlib import Path

CLAUSES = json.loads(
    Path(__file__).with_name("stadium-clauses.json").read_text(encoding="utf8")
)


def compile_stadium(en, cn):
    return next(
        (dict(c["rule"]) for c in CLAUSES if c["english"] == en and c["chinese"] == cn),
        None,
    )

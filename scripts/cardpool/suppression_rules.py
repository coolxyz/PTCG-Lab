"""Corroborated suppression contracts. Order-dependent locks are separate."""

import json
from pathlib import Path

CLAUSES = json.loads(
    Path(__file__).with_name("suppression-clauses.json").read_text(encoding="utf8")
)


def compile_suppression(en, cn):
    return next(
        (dict(c["rule"]) for c in CLAUSES if en == c["english"] and cn == c["chinese"]),
        None,
    )

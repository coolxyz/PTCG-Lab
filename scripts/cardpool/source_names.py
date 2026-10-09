"""Resolve species aliases against the separately revisioned 52poke dex list.

Card headers supply the printed species/evolution. The general list translates
those names; it does not infer an absent evolution from biological evolution.
"""

import json
from pathlib import Path

SOURCE = json.loads((Path(__file__).resolve().parents[2]/"data/cardpool/species-names.json").read_text(encoding="utf8"))
BY_NAME = {r["chinese"]:r["english"] for r in SOURCE["species"]}
BY_NUMBER = {r["number"]:r["english"] for r in SOURCE["species"]}


def evolution(header, aliases):
    from scripts.catalog.enrich import plain
    name = plain(header.get("evo",""))
    # A full article link names the same printed previous species.
    base = name.split("（",1)[0]
    fossil = {"陳舊的根狀化石": "Antique Root Fossil", "陳舊的背蓋化石": "Antique Cover Fossil"}
    if base in fossil:
        return {fossil[base]}
    if base in BY_NAME:
        return {BY_NAME[base]}
    if not name and header.get("evonumber","").isdigit():
        en = BY_NUMBER.get(int(header["evonumber"]))
        return {en} if en else set()
    return aliases.get(name,aliases.get(base,set()))

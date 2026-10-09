"""Exact modern Tool wrapper and printed attack, both independently required."""
import json
from pathlib import Path
import mwparserfromhell as mw
from scripts.catalog.enrich import params, plain

ROWS = json.loads(Path(__file__).with_name("technical-machine-clauses.json").read_text(encoding="utf8"))


def compile_machine(page):
    text = page.get("text") or ""
    ts = mw.parse(text.split("{{ExpansionList", 1)[0]).filter_templates()
    semantic = [t for t in ts if str(t.name).strip().startswith("训练家卡信息/") or str(t.name).strip() == "卡牌信息/attack"]
    actual = [(str(t.name).strip(), params(t)) for t in semantic]
    row = next((r for r in ROWS if actual == [(n, p) for n, p in r["templates"]]), None)
    if not row:
        return None
    identity = next((params(t) for t in ts if str(t.name).strip() == "N"), {})
    if plain(identity.get("4", "")) != row["name"]:
        return None
    return {"name": row["name"], "trainerType": "tool", "text": row["text"], "aceSpec": False,
            "mechanic": {"kind": "tool_modifier", "discardEndTurn": True, "grantedAttack": row["attack"]}}

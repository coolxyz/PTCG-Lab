"""Full bilingual Energy texts, including ACE SPEC header identity."""

import json
from pathlib import Path
import mwparserfromhell as mw
from scripts.catalog.enrich import params, plain

CLAUSES = json.loads(
    Path(__file__).with_name("special-energy-clauses.json").read_text(encoding="utf8")
)


def compile_energy(page):
    text = page.get("text") or ""
    if "==卡牌信息==" not in text:
        return None
    body = text.split("==卡牌信息==", 1)[1].split("{{ExpansionList", 1)[0]
    ts = mw.parse(body).filter_templates(recursive=False)
    headers = {"能量卡信息/header", "训练家卡信息/header/ACESPEC/SV"}
    if any(
        str(t.name).strip() not in headers | {"能量卡信息/main", "能量卡信息/end", "-"}
        for t in ts
    ):
        return None
    hs = [t for t in ts if str(t.name).strip() in headers]
    ms = [params(t) for t in ts if str(t.name).strip() == "能量卡信息/main"]
    if len(hs) != 1 or len(ms) != 1:
        return None
    h = params(hs[0])
    if h.get("1") == h.get("type"):
        h.pop("1", None)
    if any(v and k not in {"type", "type2", "name", "image", "disableEN"} for k, v in h.items()):
        return None
    if h.get("type") not in {"无色", "無色", "极光", "夜光", "特殊能量", "惡"} or h.get("type2") not in (None, "", "夜光", "无色"):
        return None
    p = ms[0]
    if any(
        v and k not in {"type", "effectZHS", "effectZHT", "eeffect", "jeffect", "ZHAlt"}
        for k, v in p.items()
    ):
        return None
    rule = next(
        (
            c["rule"]
            for c in CLAUSES
            if c["english"] == p.get("eeffect") and c["chinese"] == p.get("effectZHS")
        ),
        None,
    )
    if not rule:
        return None
    intro = mw.parse(text.split("==卡牌信息==", 1)[0]).filter_templates()
    identity = next((params(t) for t in intro if str(t.name).strip() == "N"), {})
    name = plain(identity.get("4", ""))
    if not name:
        return None
    return {
        "name": name,
        "energyType": "special",
        "mechanic": dict(rule),
        "text": p["effectZHS"],
        "aceSpec": str(hs[0].name).strip() == "训练家卡信息/header/ACESPEC/SV",
    }

"""Extract factual metadata and explicitly paired Chinese artwork references.

No effect text is copied and no rules implementation is certified by this parser.
"""

from pathlib import Path
import sys, json, re

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import mwparserfromhell as mw
from scripts.catalog.sources import ROOT
from scripts.catalog.parse_sets import ELEMENTS, clean_number

ELEMENTS = {
    **ELEMENTS,
    "無": "COLORLESS",
    "无": "COLORLESS",
    "龍": "DRAGON",
    "惡": "DARK",
    "鬥": "FIGHTING",
}
STAGES = {
    "基础": "BASIC",
    "基礎": "BASIC",
    "1阶进化": "STAGE_1",
    "1階進化": "STAGE_1",
    "2阶进化": "STAGE_2",
    "2階進化": "STAGE_2",
    "2阶進化": "STAGE_2",
    "V": "V_EVOLUTION",
    "M进化": "MEGA_EVOLUTION",
    "BREAK": "BREAK",
    "升级": "LEVEL_UP",
}


def plain(value):
    return str(mw.parse(str(value)).strip_code()).strip()


def params(t):
    return {str(p.name).strip(): str(p.value).strip() for p in t.params}


def number(s):
    m = re.search(r"(?:\d+\s+)?(?:[A-Za-z]*\d+|[RGB])/[\w-]+", plain(s))
    return clean_number(m[0].split("/")[0]) if m else None


def parse_page(text):
    ts = mw.parse(text or "").filter_templates()
    rows = []
    facts = {}
    attacks = []
    previous = {}
    intro = (text or "").split("==")[0]
    for t in ts:
        name = str(t.name).strip()
        p = params(t)
        if name == "N" and not facts.get("englishName"):
            facts["englishName"] = plain(p.get("4", ""))
        if name.startswith("卡牌信息/header") or name == "E卡信息/header":
            stage = STAGES.get(p.get("evostage"))
            if not stage and re.search(r"\{\{TCG\|(?:基础|基礎)宝可梦\}\}", intro):
                stage = "BASIC"
            facts.update(
                category="宝可梦",
                hp=int(p["hp"]) if p.get("hp", "").isdigit() else None,
                stage=stage,
                isBasicPokemon=stage == "BASIC" if stage else None,
                pokemonType=ELEMENTS.get(p.get("type")),
                evolvesFrom=[plain(p.get("evo", p.get("evoname")))]
                if p.get("evo") or p.get("evoname")
                else [],
            )
        if name.startswith("训练家卡信息/header"):
            facts.update(
                category="训练家",
                subtype={
                    "物品卡": "ITEM",
                    "支援者卡": "SUPPORTER",
                    "竞技场卡": "STADIUM",
                    "競技場卡": "STADIUM",
                    "宝可梦道具": "TOOL",
                    "寶可夢道具": "TOOL",
                }.get(p.get("type")),
                aceSpec="ACESPEC" in name,
            )
        if name.startswith("能量卡信息/header"):
            element = ELEMENTS.get(p.get("type"))
            facts.update(
                category="能量",
                pokemonType=element,
                basicEnergyType=("darkness" if element == "DARK" else element.lower())
                if p.get("base") == "y" and element
                else None,
            )
        if name.startswith("卡牌信息/attack"):
            costs = [p.get("cost", "")] + [
                str(x.value).strip() for x in t.params if not x.showkey
            ]
            attacks.append(
                {
                    "name": plain(
                        p.get("ZHSname")
                        or p.get("ZHTname")
                        or p.get("name")
                        or p.get("ename", "")
                    ),
                    "cost": [ELEMENTS[x] for x in costs if x in ELEMENTS],
                    "damage": p.get("damage", "")
                    + (
                        "+"
                        if p.get("damageP") == "加"
                        else "×"
                        if p.get("damageP") == "乘"
                        else ""
                    ),
                }
            )
        if name == "ExpansionList/header/zh":
            previous = {}
        if name != "ExpansionList/main/zh":
            continue
        images = []
        for prefix, locale in [("cn", "zh-Hans"), ("zh", "zh-Hant")]:
            img = p.get(prefix + "img", "")
            old = previous.get(prefix)
            if old and old["remaining"] > 0:
                old["remaining"] -= 1
            else:
                old = None
            if img and img != "n":
                item = {
                    "file": img.replace("File:", ""),
                    "locale": locale,
                    "setCode": p.get(prefix + "icon"),
                    "printedNumber": plain(p.get(prefix + "no", "")),
                    "matchMethod": "wiki-paired-printing",
                    "remaining": max(0, int(p.get(prefix + "imgrow", "1") or "1") - 1)
                    if (p.get(prefix + "imgrow", "1") or "1").isdigit()
                    else 0,
                }
                previous[prefix] = item
                images.append({k: v for k, v in item.items() if k != "remaining"})
            elif old:
                images.append({k: v for k, v in old.items() if k != "remaining"})
        rows.append({**p, "images": images})
    facts["attacks"] = attacks
    return facts, rows


def main():
    source = json.loads((ROOT / "data/catalog/card-tables.json").read_text())
    pages = json.loads((ROOT / ".catalog/card-pages.json").read_text())["pages"]
    parsed = {}
    for name, p in pages.items():
        parsed[name] = parse_page(p.get("text"))
    for c in source["cards"]:
        p = pages.get(c.get("cardPage"), {})
        facts, rows = parsed.get(c.get("cardPage"), ({}, []))
        exact = [
            r
            for r in rows
            if r.get("cnicon") == c["productCode"]
            and number(r.get("cnno", ""))
            == c.get("sourceCollectorNumber", c["collectorNumber"])
        ]
        c["tableCategory"] = c["category"]
        c.update(facts)
        c["imageCandidates"] = []
        for r in exact:
            for im in r["images"]:
                if im not in c["imageCandidates"]:
                    c["imageCandidates"].append(im)
        c["mark"] = next((r["reg"] for r in exact if r.get("reg")), None)
        c["metadataStatus"] = (
            "paired"
            if exact
            else "article-only"
            if p.get("text")
            else "missing-article"
        )
        c["cardSource"] = {k: v for k, v in p.items() if k != "text"}
    source["enrichment"] = {
        "pages": len(pages),
        "missingArticles": sum(not p.get("text") for p in pages.values()),
        "pairedCards": sum(c["metadataStatus"] == "paired" for c in source["cards"]),
        "withImageCandidates": sum(bool(c["imageCandidates"]) for c in source["cards"]),
    }
    (ROOT / "data/catalog/enriched.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n"
    )
    print(source["enrichment"])


if __name__ == "__main__":
    main()

"""Resumable presentation-only enrichment; never changes battle certification."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import csv
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import mwparserfromhell as mw
from bs4 import BeautifulSoup
from scripts.catalog.card_pages import batch
from scripts.catalog.enrich import parse_page, number, params, ELEMENTS
from scripts.catalog.artwork import origins, official_detail, match_detail, printed_key


def read(path, default=None):
    p = ROOT / path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else (default or {})


def save(path, value):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def text(value):
    value = re.sub(r"-\{\s*zh-hans:(.*?);\s*zh-hant:.*?\}-", r"\1", value, flags=re.S)
    code = mw.parse(value)
    for t in reversed(code.filter_templates()):
        p = params(t)
        name = str(t.name).strip()
        if name in ("e", "E", "能量", "Energy"):
            replacement = "[" + p.get("1", "") + "]"
        elif name in ("V", "VSTAR", "ex", "EX", "GX"):
            replacement = name + (p.get("1", "") if name == "V" and p.get("1") in ("MAX", "STAR", "-UNION") else "")
        elif name.lower() == "prism":
            replacement = "◇"
        elif name in ("C", "TCGPM", "m"):
            replacement = p.get("1", name)
        else:
            replacement = p.get("2") or p.get("1") or name
        code.replace(t, replacement)
    value = re.sub(r"<br\s*/?>", "\n", str(code), flags=re.I)
    return BeautifulSoup(str(mw.parse(value).strip_code()), "html.parser").get_text().strip()


def chinese(p, field):
    keys = ("ZHSname", "titleZHS", "ZHTname", "titleZHT", "cname", "name") if field == "name" else ("effectZHS", "effectZHT", "ceffect")
    for key in keys:
        if p.get(key):
            return text(p[key]), "zh-Hant" if "ZHT" in key else "zh-Hans"
    return "", None


def description(raw):
    sections = []
    templates = mw.parse(raw or "").filter_templates()
    rules = {}
    def rule(name, effect, template):
        rules[name] = {"kind": "规则", "name": name, "text": effect, "locale": "zh-Hans", "source": "https://wiki.52poke.com/wiki/" + quote("Template:" + template)}
    classes = set()
    for t in templates:
        name, p = str(t.name).strip(), params(t)
        if name.startswith("卡牌信息/header") or name in ("卡牌信息/ssend", "卡牌信息/smend", "卡牌信息/svend"):
            classes.update(p.get(k) for k in ("class", "class3") if p.get(k))
            if name.rsplit("/", 1)[-1] in ("GX", "V", "VSTAR", "VMAX"):
                classes.add(name.rsplit("/", 1)[-1])
        if name == "卡牌信息/header/太晶":
            rule("太晶", "只要这只宝可梦在备战区，就不会受到招式的伤害。", name)
        if name in ("卡牌信息/power/VSTAR", "卡牌信息/attack/VSTAR"):
            rule("VSTAR 力量限制", "本场对战中，己方只能使用1次 VSTAR 力量。", name)
        if name == "卡牌信息/attack/GX":
            rule("GX 招式限制", "本场对战中，己方只能使用1次 GX 招式。", name)
        if name.startswith("卡牌信息/attack") or name == "卡牌信息/宝可梦/move":
            kind = "GX招式" if name.endswith("/GX") else "VSTAR招式" if name.endswith("/VSTAR") else "招式"
        elif name.startswith("卡牌信息/power") or name == "卡牌信息/宝可梦/ability":
            kind = "VSTAR特性" if name.endswith("/VSTAR") else "特性"
        elif name in ("训练家卡信息/main", "训练家卡信息/multimain", "能量卡信息/main", "E卡信息/训练家卡"):
            kind = "效果"
        else:
            continue
        title, _ = chinese(p, "name")
        effect, locale = chinese(p, "effect")
        costs = [p.get("cost", "")] + [str(x.value).strip() for x in t.params if not x.showkey]
        for nested in mw.parse(p.get("costs", "")).filter_templates():
            if str(nested.name).strip() == "E/m": costs.extend(str(x.value).strip() for x in nested.params)
        section = {"kind": kind, "name": title or kind, "text": effect, "locale": locale,
                   "cost": [ELEMENTS[x] for x in costs if x in ELEMENTS],
                   "damage": p.get("damage", "") + {"加": "+", "乘": "×", "减": "−"}.get(p.get("damageP"), "")}
        if not effect and any(text(p.get(k, "")) for k in ("eeffect", "jeffect", "effectEN", "effectJA")):
            section["missingText"] = True
        if name.endswith("multimain"):
            section["versionLabel"] = text(p.get("ver1", ""))
        sections.append(section)
    if classes & {"TAG TEAM", "VMAX", "V-UNION", "exm", "ex-m"}:
        source = "卡牌信息/end/TAG TEAM" if "TAG TEAM" in classes else "卡牌信息/end/ex" if classes & {"exm", "ex-m"} else "卡牌信息/ssend"
        rule("昏厥奖赏规则", "这只宝可梦昏厥时，对手拿取3张奖赏卡。", source)
    elif classes & {"GX", "V", "VSTAR", "ex", "ext", "ex-t"}:
        source = "卡牌信息/end/GX" if "GX" in classes else "卡牌信息/end/ex" if classes & {"ex", "ext", "ex-t"} else "卡牌信息/ssend"
        rule("昏厥奖赏规则", "这只宝可梦昏厥时，对手拿取2张奖赏卡。", source)
    if not sections:
        overview = re.split(r"==\s*PTCG Pocket", raw or "", maxsplit=1)[0]
        effect = re.search(r"效果为\s*'''[“「](.*?)[”」]", overview, re.S)
        if effect:
            sections.append({"kind": "效果", "name": "效果", "text": text(effect[1]), "locale": "zh-Hans"})
    return sections + list(rules.values())


def page_assignments(cards, pages, parsed):
    """Recover early seed entries only from exact CN set+number evidence."""
    by_printing = {}
    for title, (_, rows) in parsed.items():
        for r in rows:
            key = (r.get("cnicon"), number(r.get("cnno", "")))
            by_printing.setdefault(key, set()).add(title)
    for row in read("artifacts/cardpool/catalog-inventory.json").get("records", []):
        if row.get("cardPage") in pages and not row.get("foreign"):
            key = (row.get("productCode"), row.get("collectorNumber"))
            by_printing.setdefault(key, set()).add(row["cardPage"])
    assigned = {}
    by_effect = {}
    for c in cards:
        if c.get("effectStatus") == "verified" and c.get("cardPage") in pages:
            by_effect.setdefault(c["engineId"], set()).add(c["cardPage"])
    for c in cards:
        title = c.get("cardPage")
        if not title:
            choices = by_printing.get((c.get("productCode"), c.get("collectorNumber")), set())
            canonical = {pages[t].get("title", t): t for t in choices}
            if len(canonical) == 1: title = next(iter(canonical.values()))
            if not title and c.get("effectStatus") == "verified":
                # Already-reviewed reprints share the exact engine effect;
                # this is not a name-based equivalence inference.
                choices = by_effect.get(c["engineId"], set())
                canonical = {pages[t].get("title", t): t for t in choices}
                if len(canonical) == 1: title = next(iter(canonical.values()))
            if c.get("basicEnergyType"):
                candidate = c["cnName"] + "（TCG）"
                if candidate in pages: title = candidate
        assigned[c["printingId"]] = title
    return assigned


OFFICIAL_SECTIONS = {}


def official_sections(detail):
    if detail["source"] in OFFICIAL_SECTIONS:
        return OFFICIAL_SECTIONS[detail["source"]]
    path = ROOT / detail.get("cachePath", "")
    if not path.is_file(): return []
    raw = path.read_text(encoding="utf-8")
    column = re.search(r'<section\s+class="cardInformationColumn"[^>]*>.*?</section>', raw, re.S)
    soup = BeautifulSoup(column[0] if column else raw, "html.parser")
    sections = []
    for skill in soup.select(".cardInformationColumn .skill"):
        def value(selector):
            el = skill.select_one(selector)
            return el.get_text(" ", strip=True) if el else ""
        title, effect = value(".skillName"), value(".skillEffect")
        if not title and not effect: continue
        header = skill.parent.select_one("h3")
        kind = "规则" if "規則" in title or title == "[太晶]" else "特性" if "特性" in title or (header and "特性" in header.get_text()) else "招式" if title else "效果"
        costs = [Path(im.get("src", "")).stem.upper() for im in skill.select(".skillCost img")]
        sections.append({"kind": kind, "name": title or kind, "text": effect, "damage": value(".skillDamage"), "cost": costs, "locale": "zh-Hant", "source": detail["source"]})
    OFFICIAL_SECTIONS[detail["source"]] = sections
    return sections


def acquire(titles, pages, path):
    todo = sorted(t for t in titles if t not in pages)
    errors = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(batch, todo[i:i+20]): todo[i:i+20] for i in range(0, len(todo), 20)}
        for j in as_completed(jobs):
            try:
                pages.update(j.result())
            except Exception as e:
                for t in jobs[j]: errors[t] = str(e)
            save(path, {"pages": pages, "errors": errors})
    return errors


def traditional_candidates(rows):
    result = []
    for row in rows:
        result.extend(im for im in row["images"] if im["locale"] == "zh-Hant")
        if row.get("zhicon") and number(row.get("zhno", "")):
            result.append({"locale": "zh-Hant", "setCode": row["zhicon"], "printedNumber": text(row["zhno"]), "matchMethod": "wiki-listed-traditional-printing"})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    cards = read("data/catalog/catalog.json")["cards"]
    pages = {}
    for path in (".catalog/card-pages.json", ".catalog/cardpool/card-pages.json", ".catalog/details-pages.json"):
        pages.update(read(path).get("pages", {}))
    if args.fetch:
        acquire({c["cardPage"] for c in cards if c.get("cardPage")}, pages, ".catalog/details-pages.json")
    parsed = {title: (description(p.get("text")), parse_page(p.get("text"))[1]) for title, p in pages.items()}
    assigned = page_assignments(cards, pages, parsed)
    candidates = {}
    for c in cards:
        rows = parsed.get(assigned[c["printingId"]], ([], []))[1]
        paired = [r for r in rows if r.get("cnicon") == c.get("productCode") and number(r.get("cnno", "")) == c.get("sourceCollectorNumber", c.get("collectorNumber"))]
        # Same article denotes one card effect; use labelled substitute art when
        # an exact CN row is unavailable, never imply identical illustration.
        selected = paired or rows
        candidates[c["printingId"]] = traditional_candidates(selected)
        # A paired version may lack an image; other Traditional printings of
        # this same card article remain eligible as explicitly labelled substitutes.
        for candidate in traditional_candidates(rows):
            if candidate not in candidates[c["printingId"]]:
                candidates[c["printingId"]].append(candidate)
    files = read(".catalog/image-pages.json").get("pages", {})
    files.update(read(".catalog/details-image-pages.json").get("pages", {}))
    if args.fetch:
        acquire({"File:" + im["file"] for ims in candidates.values() for im in ims if im.get("file")}, files, ".catalog/details-image-pages.json")
    official = read("data/catalog/official-artwork.json").get("details", {})
    official.update(read(".catalog/details-official.json").get("details", {}))
    official.update(read(".catalog/search-official.json").get("details", {}))
    official.update(read(".catalog/search-prefetch.json").get("details", {}))
    official.update(read(".catalog/search-variants.json").get("details", {}))
    official.update(read(".catalog/search-broad.json").get("details", {}))
    official.update(read("data/catalog/official-listings.json").get("details", {}))
    urls = {u for p in files.values() for u in origins(p.get("text")) if "asia.pokemon-card.com" in u}
    errors = {}
    if args.fetch:
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(official_detail, u): u for u in sorted(urls - official.keys())}
            for n, j in enumerate(as_completed(jobs), 1):
                try: official[jobs[j]] = j.result()
                except Exception as e: errors[jobs[j]] = str(e)
                if n % 40 == 0 or n == len(jobs):
                    save(".catalog/details-official.json", {"details": official, "errors": errors})
                    print("Official", n, "/", len(jobs), "errors", len(errors), flush=True)
    index = {}
    for d in official.values():
        if d.get("locale") == "zh-Hant" and d.get("setCode"):
            from scripts.catalog.artwork import set_key
            index.setdefault((set_key(d["setCode"]), printed_key(d["printedNumber"])), []).append(d)
    result, gaps = {}, []
    from packages.rules.catalog import select_image
    for c in cards:
        pid = c["printingId"]
        p = pages.get(assigned[pid], {})
        sections = parsed.get(assigned[pid], ([], []))[0]
        if not sections and c.get("basicEnergyType") and p.get("text"):
            sections = [{"kind": "能量", "name": c["cnName"], "text": text(p["text"].split("==")[0]), "locale": "zh-Hans"}]
        item = {"sections": sections, "source": p.get("sourceUrl") or c.get("cardUrl") or next((u for u in c.get("sourceEvidence", []) if "wiki.52poke.com" in u), None), "revision": p.get("revision"),
                "textStatus": "missing-page" if not p.get("text") else "partial" if any(s.get("missingText") for s in sections) else "available" if sections else "no-effect-text"}
        existing = select_image(c)
        for im in candidates[pid]:
            key = (set_key(im.get("setCode") or ""), printed_key(im.get("printedNumber")))
            matches = [d for d in index.get(key, []) if match_detail(im, c, d)]
            if matches:
                d = sorted(matches, key=lambda d: d["source"])[0]
                official_text = official_sections(d)
                if item["textStatus"] == "partial" and official_text:
                    item["sections"] = official_text + [s for s in sections if s["kind"] == "规则"]
                    item["textStatus"] = "official-fallback"
                elif official_text:
                    item["sections"] = sections + [s for s in official_text if s["kind"] == "规则" and not (s["name"] == "[太晶]" and any(r["name"] == "太晶" for r in sections))]
                if not existing.get("url"):
                    item["image"] = {**d, "label": "繁中卡图", "equivalenceReviewed": True,
                                     "matchMethod": "same-wiki-article+official-set-number-hp", "wikiSource": item["source"]}
                break
        reason = ""
        if not existing.get("url") and not item.get("image"):
            reason = "百科卡牌页面缺失" if not p.get("text") else "未找到繁中收录卡图线索" if not candidates[pid] else "未能从官网核对对应版本卡图"
            gaps.append({"printingId": pid, "cnName": c["cnName"], "productCode": c.get("productCode"), "collectorNumber": c.get("collectorNumber"), "reason": reason, "source": item["source"]})
        item["imageGap"] = reason
        result[pid] = item
    errors = {**read(".catalog/details-official.json").get("errors", {}), **read(".catalog/search-official.json").get("errors", {}), **read(".catalog/search-variants.json").get("errors", {}), **errors}
    completed_searches = {**read(".catalog/search-official.json").get("searches", {}), **read(".catalog/search-variants.json").get("searches", {})}
    completed_searches.update(read(".catalog/search-broad.json").get("searches", {}))
    errors.update(read(".catalog/search-broad.json").get("errors", {}))
    errors = {key: value for key, value in errors.items() if key not in official and key not in completed_searches}
    report = {"cards": len(cards), "textStatuses": dict(Counter(x["textStatus"] for x in result.values())), "addedImages": sum(bool(x.get("image")) for x in result.values()), "missingImages": len(gaps), "officialErrors": errors}
    save("data/catalog/card-details.json", {"cards": result, "report": report})
    save("artifacts/catalog/missing-images.json", {"summary": report, "cards": gaps})
    save("artifacts/catalog/missing-descriptions.json", {"cards": [{"printingId": c["printingId"], "cnName": c["cnName"], **result[c["printingId"]]} for c in cards if result[c["printingId"]]["textStatus"] in ("missing-page", "partial", "no-effect-text")]})
    with (ROOT / "artifacts/catalog/missing-images.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["卡牌ID", "名称", "扩充包编号", "卡号", "原因", "百科来源"])
        for row in gaps:
            values = [row.get(k) for k in ("printingId", "cnName", "productCode", "collectorNumber", "reason", "source")]
            writer.writerow(["'" + str(v) if str(v).startswith(("=", "+", "-", "@")) else v for v in values])
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__": main()

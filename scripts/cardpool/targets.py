"""Enumerate all CN product tables, retaining unresolved source facts explicitly."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup  # noqa: E402
from scripts.catalog.parse_sets import table_grid, release_dates, clean_number  # noqa: E402
from scripts.catalog.enrich import parse_page, number  # noqa: E402
from scripts.catalog.corrections import apply_number_correction  # noqa: E402


def tables(html):
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for index, table in enumerate(soup.find_all("table")):
        direct = table.select(":scope > tbody > tr") or table.find_all(
            "tr", recursive=False
        )
        if not direct:
            continue
        head = direct[0].find_all(["th", "td"], recursive=False)
        if (
            len(head) < 2
            or head[0].get_text(strip=True) != "编号"
            or "卡牌" not in head[1].get_text()
        ):
            continue
        for cell in table.find_all(["th", "td"]):
            for attr in ("colspan", "rowspan"):
                if not cell.get(attr, "1").isdigit():
                    cell[attr] = "1"
        symbol = table.find_previous("a", href=re.compile(r"SetSymbol[^/]+\.png"))
        code = (
            re.search(r"SetSymbol(.+?)\.png", unquote(symbol["href"]))[1]
            if symbol
            else None
        )
        rows, skipped = [], []
        for position, cells in enumerate(table_grid(table)[1:], 2):
            if len(cells) < 2 or any(c is None for c in cells[:2]):
                skipped.append({"row": position, "reason": "incomplete-row"})
                continue
            label = cells[0].get_text(" ", strip=True)
            match = re.match(r"(?:\d+\s+)?(?:[A-Za-z]*\d+|[RGB])/([\w-]+)", label)
            if not match:
                skipped.append(
                    {
                        "row": position,
                        "reason": "non-card-or-unrecognized-number",
                        "text": label[:200],
                    }
                )
                continue
            card_code = (
                match[1]
                if match[1] in ("SM-P", "S-P", "SV-P", "30th-P", "M-P")
                else code
            )
            link = cells[1].find("a", title=True)
            rows.append(
                {
                    "collectorNumber": clean_number(match[0].split("/")[0]),
                    "printedNumber": match[0],
                    "cnName": cells[1].get_text(" ", strip=True),
                    "productCode": card_code,
                    "cardPage": re.sub(r"（页面不存在）$", "", link["title"])
                    if link
                    else None,
                    "table": index,
                    "row": position,
                    "sourceRow": position,
                    "availabilityText": " | ".join(
                        c.get_text(" ", strip=True) for c in cells[2:] if c
                    ),
                    "evidenceUrls": list(
                        dict.fromkeys(
                            a["href"]
                            for c in cells[2:]
                            if c
                            for a in c.find_all("a", href=True)
                            if a["href"].startswith("https://www.pokemon.cn/")
                        )
                    ),
                }
            )
        result.append({"table": index, "code": code, "rows": rows, "skipped": skipped})
    return result


def build(source, pages):
    rules = json.loads(
        (ROOT / "rulesets/cn-standard-2026-09-16.json").read_text(encoding="utf-8")
    )
    catalog = json.loads(
        (ROOT / "data/catalog/catalog.json").read_text(encoding="utf-8")
    )
    existing = {c["printingId"]: c for c in catalog["cards"]}
    corrections = json.loads(
        (ROOT / "data/catalog/number-overrides.json").read_text(encoding="utf-8")
    )["corrections"]
    parsed_pages = {}
    cards, products, conflicts, unresolved = {}, [], [], []
    for product in source["products"]:
        src = product.get("source", {})
        path = ROOT / src.get("cachePath", "missing")
        row = {
            "title": product["title"],
            "source": src,
            "metadata": product.get("metadata", {}),
        }
        if not path.is_file() or hashlib.sha256(
            path.read_bytes()
        ).hexdigest() != src.get("sha256"):
            row["status"] = "source-missing-or-changed"
            products.append(row)
            continue
        parsed = json.loads(path.read_text(encoding="utf-8"))["parse"]
        ts = tables(parsed["text"]["*"])
        dates = release_dates(row["metadata"].get("发布时间", ""))
        row.update(
            status="enumerated" if ts else "no-card-table",
            tables=len(ts),
            rows=sum(len(t["rows"]) for t in ts),
            releaseDates=dates,
            skipped=[{"table": t["table"], **r} for t in ts for r in t["skipped"]],
        )
        products.append(row)
        for ti, table in enumerate(ts):
            for item in table["rows"]:
                code = item["productCode"]
                if not code or not (code.endswith("C") or code.endswith("-P")):
                    unresolved.append(
                        {
                            **item,
                            "product": product["title"],
                            "reason": "CN_SET_CODE_UNRESOLVED",
                        }
                    )
                    continue
                # Multi-edition pages cannot assign a later printing the earliest
                # product release without evidence. All table associations survive.
                release = (
                    dates[ti]
                    if len(dates) == len(ts)
                    else dates[0]
                    if len(set(dates)) == 1
                    else max(dates)
                    if dates and max(dates) <= source["asOf"]
                    else None
                )
                correction_error = None
                try:
                    corrected = apply_number_correction(
                        item, code, src["revision"], corrections
                    )
                except ValueError as exc:
                    corrected, correction_error = item, str(exc)
                pid = f"CN:{code}:{corrected['collectorNumber']}"
                page = pages.get(item.get("cardPage"), {})
                title = page.get("title", item.get("cardPage"))
                if title not in parsed_pages:
                    parsed_pages[title] = parse_page(page.get("text"))
                facts, paired = parsed_pages[title]
                exact = [
                    r
                    for r in paired
                    if r.get("cnicon") == code
                    and number(r.get("cnno", "")) == item["collectorNumber"]
                ]
                marks = {r["reg"] for r in exact if r.get("reg")}
                mark = next(iter(marks)) if len(marks) == 1 else None
                baseline = existing.get(pid, {})
                # A P4 reprint derives its facts from this inventory. Feeding its
                # published bound back here would turn derived evidence into an
                # independent source and overwrite the original product dates.
                if (
                    baseline.get("sourceVerified")
                    and baseline.get("reviewScope") != "p4-equivalent-reprint-v1"
                ):
                    release = baseline.get("releasedAt", release)
                    mark = baseline.get("mark", mark)
                released = (
                    "released"
                    if release and release <= source["asOf"]
                    else "future"
                    if release
                    else "unknown"
                )
                status = (
                    "current-mark"
                    if mark in rules["allowedMarks"]
                    else "basic-energy"
                    if facts.get("basicEnergyType") in rules["basicEnergyTypes"]
                    else "legacy-reprint-review"
                    if item["cnName"] in rules["legacyReprintNames"]
                    else "not-current-mark"
                    if mark
                    else "format-unresolved"
                )
                gaps = []
                if correction_error:
                    gaps.append("NUMBER_CORRECTION_REVIEW_REQUIRED")
                if not page.get("text"):
                    gaps.append("CARD_ARTICLE_MISSING")
                if not exact:
                    gaps.append("PRINTING_PAIR_UNRESOLVED")
                if len(marks) > 1:
                    gaps.append("REGULATION_MARK_CONFLICT")
                if released == "unknown":
                    gaps.append("RELEASE_EVIDENCE_REQUIRED")
                if not baseline.get("sourceVerified"):
                    gaps.append("PRINTING_REVIEW_REQUIRED")
                if baseline.get("effectStatus") != "verified":
                    gaps.append("RULE_EFFECT_UNREVIEWED")
                evidence = {
                    "product": product["title"],
                    "source": src,
                    "table": item["table"],
                    "row": item["row"],
                    "releaseBound": release,
                    "availabilityText": item["availabilityText"],
                    "evidenceUrls": item["evidenceUrls"],
                }
                card = {
                    **corrected,
                    "printingId": pid,
                    "cardPage": title,
                    "facts": facts,
                    "mark": mark,
                    "releaseStatus": released,
                    "releasedBy": release,
                    "formatStatus": status,
                    "engineId": baseline.get("engineId")
                    if baseline.get("effectStatus") == "verified"
                    else None,
                    "gaps": gaps,
                    "cardSource": {k: v for k, v in page.items() if k != "text"},
                    "sources": [evidence],
                }
                for field in ("table", "row", "availabilityText", "evidenceUrls"):
                    card.pop(field, None)
                if pid in cards:
                    if cards[pid]["cardPage"] != title:
                        conflicts.append(
                            {
                                "printingId": pid,
                                "cardPages": [cards[pid]["cardPage"], title],
                                "source": evidence,
                            }
                        )
                    cards[pid]["sources"].append(evidence)
                    if (
                        released == "released"
                        and cards[pid]["releaseStatus"] != "released"
                    ):
                        cards[pid].update(
                            releaseStatus=released,
                            releasedBy=release,
                            gaps=[
                                g
                                for g in cards[pid]["gaps"]
                                if g != "RELEASE_EVIDENCE_REQUIRED"
                            ],
                        )
                else:
                    cards[pid] = card
    for conflict in conflicts:
        card = cards[conflict["printingId"]]
        if "PRINTING_IDENTITY_CONFLICT" not in card["gaps"]:
            card["gaps"].append("PRINTING_IDENTITY_CONFLICT")
    # Baseline synthetic basic-energy identities remain explicit, not invented
    # product printings. Preserve them as existing canonical energy references.
    current = [
        c
        for c in cards.values()
        if c["releaseStatus"] == "released"
        and c["formatStatus"]
        in ("current-mark", "basic-energy", "legacy-reprint-review")
    ]
    return {
        "schema": "p4-target-inventory-v1",
        "asOf": source["asOf"],
        "formatId": rules["id"],
        "complete": False,
        "counts": {
            "products": len(products),
            "printings": len(cards),
            "released": sum(c["releaseStatus"] == "released" for c in cards.values()),
            "releaseUnknown": sum(
                c["releaseStatus"] == "unknown" for c in cards.values()
            ),
            "future": sum(c["releaseStatus"] == "future" for c in cards.values()),
            "currentFormatCandidates": len(current),
            "candidateRulePages": len({c["cardPage"] for c in current}),
            "gaps": dict(Counter(g for c in cards.values() for g in c["gaps"])),
            "identityConflicts": len(conflicts),
            "unresolvedRows": len(unresolved),
        },
        "products": products,
        "cards": sorted(cards.values(), key=lambda c: c["printingId"]),
        "conflicts": conflicts,
        "unresolvedRows": unresolved,
        "limits": [
            "Candidate status is not a legality or effect certificate",
            "Unknown promo release dates and source conflicts block completeness",
            "Card articles are pinned to recorded cached revisions; new errata require explicit review",
            "Synthetic basic-energy IDs remain existing canonical references, not catalogued physical printings",
        ],
    }


if __name__ == "__main__":
    source = json.loads(
        (ROOT / "data/cardpool/source-index.json").read_text(encoding="utf-8")
    )
    if not source.get("completeFetch"):
        raise SystemExit("SOURCE_FREEZE_INCOMPLETE")
    pages = json.loads((ROOT / ".catalog/card-pages.json").read_text(encoding="utf-8"))[
        "pages"
    ]
    extra = ROOT / ".catalog/cardpool/card-pages.json"
    if extra.exists():
        pages.update(json.loads(extra.read_text(encoding="utf-8"))["pages"])
    report = build(source, pages)
    out = ROOT / "artifacts/cardpool"
    out.mkdir(exist_ok=True)
    (out / "target-inventory.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))

"""Offline, reproducible inventory. Unknown identities never become certified."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def read(relative):
    return json.loads((ROOT / relative).read_text())


def normalized(url):
    return unquote(url).replace("_", " ")


def build():
    from packages.battle.runtime import ENGINE_VERSION
    from ptcg.core.card_registry import registry

    catalog = read("data/catalog/catalog.json")
    rules = read("rulesets/cn-standard-2026-09-16.json")
    # Products are owned by the pinned current catalogue. The historical Wiki
    # crawl is no longer a production dependency.
    sources = {"products": [{"title": p["name"], "url": p.get("source", {}).get("url", ""),
                             "metadata": {"releasedAt": p.get("releasedAt"), "id": p["id"]}}
                            for p in catalog["products"]]}
    cards, groups = [], defaultdict(list)
    for card in catalog["cards"]:
        effect = card.get("engineId", "")
        reviewed = card.get("effectStatus") == "verified" and not effect.startswith(
            "unverified:"
        )
        available = bool(reviewed and registry.get(effect))
        gaps = []
        if not card.get("sourceVerified"):
            gaps.append("PRINTING_REVIEW_REQUIRED")
        if not reviewed:
            gaps += ["RULE_IDENTITY_UNREVIEWED", "EFFECT_IMPLEMENTATION_UNVERIFIED"]
        elif not available:
            gaps.append("REGISTERED_EFFECT_MISSING")
        if not card.get("mark") and not card.get("basicEnergyType"):
            gaps.append("FORMAT_FACTS_INCOMPLETE")
        # This is a candidate filter, not a legality certificate. Older marks
        # remain in the inventory for reprint-exception review.
        candidate = (
            "reviewed-baseline"
            if reviewed
            else "allowed-mark-candidate"
            if card.get("mark") in rules["allowedMarks"]
            else "basic-energy-candidate"
            if card.get("basicEnergyType")
            else "reprint-or-format-review"
        )
        row = {
            "printingId": card["printingId"],
            "name": card["cnName"],
            "productCode": card.get("productCode"),
            "mark": card.get("mark"),
            "formatReview": candidate,
            "effectKey": effect if reviewed else None,
            "status": "reviewed-effect" if available else "unverified",
            "gaps": gaps,
            "sources": card.get("sourceEvidence", []),
        }
        cards.append(row)
        if reviewed:
            groups[effect].append(card["printingId"])
    known_urls = {
        normalized(u) for c in catalog["cards"] for u in c.get("sourceEvidence", [])
    }
    products = []
    for p in sources["products"]:
        title = p["title"]
        kind = (
            "promo"
            if "特典" in title
            else "preconstructed"
            if any(x in title for x in ["卡组", "套装", "礼盒"])
            else "pack-or-other"
        )
        linked = normalized(p["url"]) in known_urls
        products.append(
            {
                "title": title,
                "url": p["url"],
                "kind": kind,
                "releaseMetadata": p.get("metadata", {}),
                "status": "requires-completeness-review"
                if linked
                else "not-linked-to-catalog",
                "sourceError": p.get("error"),
            }
        )
    effects = []
    for key, printings in sorted(groups.items()):
        cls = registry.get(key)
        obj = cls() if cls else None
        effects.append(
            {
                "effectKey": key,
                "printings": printings,
                "name": obj.name if obj else None,
                "registered": obj is not None,
                "implementation": f"{cls.__module__}.{cls.__name__}" if cls else None,
                "scope": "current-reviewed-effect-pool",
                "evidenceSuite": ["tests/rules", "tests/simulation", "tests/cardpool", "tests/sync"],
                "interactionAudit": "required-for-arbitrary-combinations",
            }
        )
    inputs = [
        "data/catalog/catalog.json",
        "data/simulation/effects.json",
        "rulesets/cn-standard-2026-09-16.json",
        "scripts/simulation/inventory.py",
    ]
    return {
        "schema": "p3-inventory-v1",
        "catalogVersion": catalog["version"],
        "engineVersion": ENGINE_VERSION,
        "formatId": rules["id"],
        "targetCompleteness": "INCOMPLETE",
        "limitations": [
            "Inventory is of existing local evidence, not all legal CN printings.",
            "Promos, preconstructed products and reprint exceptions require full source review.",
            "Unreviewed rule identities are not deduplicated by card name.",
            "Engine registration alone does not prove rule correctness or AI competence.",
        ],
        "counts": {
            "printings": len(cards),
            "reviewedPrintings": sum(
                c["status"] == "reviewed-effect" for c in cards
            ),
            "reviewedEffects": len(effects),
            "products": len(products),
            "formatReview": dict(Counter(c["formatReview"] for c in cards)),
            "gaps": dict(Counter(g for c in cards for g in c["gaps"])),
            "productStatus": dict(Counter(p["status"] for p in products)),
        },
        "sourceHashes": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in inputs
        },
        "effects": effects,
        "products": products,
        "cards": cards,
    }


if __name__ == "__main__":
    result = build()
    out = ROOT / "artifacts/simulation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "inventory.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {"targetCompleteness": result["targetCompleteness"], **result["counts"]},
            ensure_ascii=False,
            indent=2,
        )
    )
